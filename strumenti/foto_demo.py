"""
Foto dei prodotti della demo, generate con OpenAI (28/09/2026).

I prodotti della demo sono inventati (candele, oli, tessili, carta...): al posto delle iniziali servono foto credibili
e coerenti fra loro. Uno STILE comune, con i colori dell'app (luce dorata, fondi crema, mai stock generico),
più un SOGGETTO per prodotto; i prodotti con varianti di colore hanno una foto per colore.
Nessuna foto contiene testo, loghi, marchi o persone: sono foto generate, e il portfolio lo dichiara.

La chiave non sta in questo progetto: si legge da OPENAI_API_KEY nell'ambiente oppure dal file .env indicato
con --env (per esempio un .env in un'altra cartella). Lo script non la stampa mai.

  python strumenti/foto_demo.py --elenco                      # prompt e nomi dei file, senza chiamare OpenAI
  python strumenti/foto_demo.py --genera --env ..\\.env       # genera solo le foto che mancano
  python strumenti/foto_demo.py --genera --env ..\\.env candela-profumata-ambra   # rifà quelle indicate

Uscita: magazzino/static/demo/prodotti/<file>.jpg (512x512), originali PNG in dati/foto_demo/ (fuori da git).
"""
import base64, json, os, re, sys, threading, time, unicodedata, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

QUI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(QUI))
from magazzino.demo import PRODOTTI, nome_foto  # noqa: E402

USCITA = QUI / "magazzino" / "static" / "demo" / "prodotti"
ORIGINALI = QUI / "dati" / "foto_demo"

STILE = (
    "Square e-commerce product photograph (packshot) for the catalog of an upscale Italian lifestyle boutique. "
    "Mood: warm, elegant, refined and welcoming. "
    "One single product, centered, filling about two thirds of the frame, seen at a gentle three-quarter angle, "
    "fully visible and not cropped. "
    "Background: seamless warm cream backdrop (#FCF8F3 fading to ivory #F6EFE8), the same for every product of the "
    "catalog; a soft natural shadow under the product. "
    "Light: soft, warm, golden, diffused window light, bright mid-key exposure, luminous and airy, never dark. "
    "Colors natural and coherent, not oversaturated; small accents allowed in soft blush pink, dusty rose, "
    "deep bordeaux and champagne gold. "
    "Photographic realism, crisp focus on the product, subtle natural film grain, high-end magazine quality, "
    "never glossy or overproduced; at most one or two small props, only those named in the subject. "
    "Avoid: hard or cold flash, clinical pure-white background, generic stock-photo look, busy scenes. "
    "Strictly no text, no letters, no numbers, no logos, no brand names, no labels, no printed packaging, "
    "no people, no hands, no faces."
)

# prodotto (come in demo.PRODOTTI) -> soggetto in inglese; {colore} si riempie con il colore della variante
SOGGETTI = {
    "Candela profumata ambra": "a lit scented candle in a ribbed amber glass tumbler, a small warm flame",
    "Candela profumata fico": "a scented candle in a matte sage-green ceramic vessel, one fresh fig beside it",
    "Candela profumata vaniglia": "a scented candle in a matte ivory ceramic vessel, two dark vanilla pods beside it",
    "Diffusore bastoncini agrumi": "a reed diffuser: a clear glass bottle with pale amber liquid and thin natural rattan "
                                   "reeds fanning out, half an orange slice beside it",
    "Olio da massaggio mandorla": "an amber glass bottle of massage oil with a brushed brass pump dispenser, a few whole "
                                  "almonds beside it",
    "Olio da massaggio arnica": "a small amber glass dropper bottle of massage oil, a couple of yellow arnica flowers beside it",
    "Crema mani al karité": "a soft ivory aluminium tube of hand cream with a small swirl of cream beside it",
    "Crema corpo cocco": "a wide matte white jar of body cream with the lid leaning on it, whipped cream texture visible, "
                         "half a coconut beside it",
    "Scrub corpo sale marino": "a glass jar of coarse sea salt body scrub with a small wooden spoon",
    "Bagnoschiuma lavanda": "a frosted glass bottle of lavender body wash with a sprig of lavender",
    "Sali da bagno eucalipto": "a glass jar of pale green bath salts with a few eucalyptus leaves",
    "Burrocacao miele": "a small lip balm tin, lid open, next to a wooden honey dipper",
    "Accappatoio in cotone": "a neatly folded ivory waffle-weave cotton bathrobe",
    "Kimono in raso": "a blush pink satin kimono robe neatly folded with its sash, soft sheen",
    "Pigiama in modal": "a folded pajama set in soft dusty rose modal jersey",
    "Calze in cashmere": "a pair of neatly folded cashmere socks in {colore}",
    "Pantofole in feltro": "a pair of grey wool felt slippers",
    "Mascherina per dormire in seta": "a silk sleep mask in {colore}, softly draped",
    "Tazza in ceramica": "a handmade ceramic mug in {colore} glaze",
    "Teiera in ghisa": "a black cast iron Japanese teapot with a subtle dotted texture",
    "Infuso rilassante": "a cup of chamomile herbal tea with a few dried chamomile flowers beside it",
    "Infuso zenzero e limone": "a glass cup of ginger and lemon infusion with a piece of fresh ginger root and a lemon slice",
    "Tisana notte": "a ceramic cup of herbal night tea with dried lavender and linden flowers beside it",
    "Miele millefiori": "a glass jar of golden wildflower honey with a wooden honey dipper",
    "Cioccolato fondente 70%": "a bar of dark chocolate partly unwrapped from plain bordeaux paper, two broken squares",
    "Biscotti al burro": "a small stack of round butter shortbread cookies on a small plate",
    "Libro di ricette vegetariane": "a closed hardcover book with a plain sage-green linen cover, a sprig of fresh basil on it",
    "Agenda 2027": "a closed A5 planner with a plain bordeaux leather-like cover and an elastic band",
    "Taccuino a righe": "a closed notebook with a plain cream cover and a thin ribbon bookmark",
    "Penna stilografica": "a black fountain pen with a gold nib, the cap beside it",
    "Borraccia termica": "a matte cream stainless steel insulated water bottle",
    "Beauty case": "a quilted blush pink cosmetic bag with a gold zipper",
    "Pochette in lino": "a natural linen zip pouch",
    "Specchio da borsa": "an open round compact pocket mirror in brushed brass",
    "Pettine in legno": "a wooden comb made of warm pear wood",
    "Spazzola in setole naturali": "a wooden hairbrush with natural boar bristles",
    "Lima per unghie in vetro": "a glass nail file resting on its slim blush pink case",
    "Fascia per capelli": "a knotted satin headband in {colore}",
    "Profumo per ambienti rosa": "a clear glass room spray bottle with pale pink liquid and a few rose petals",
    "Gel igienizzante mani": "a small clear bottle of hand sanitizer gel with a flip cap",
    "Set regalo relax": "an open gift box with a small candle, a small bottle of massage oil and a jar of bath salts, "
                        "a bordeaux ribbon",
    "Carta regalo": "a roll of wrapping paper with a subtle cream and blush pattern, partly unrolled, with a satin ribbon",
    "Buono regalo cartaceo": "a blank cream gift card half out of its envelope, sealed with a bordeaux wax seal",
}
COLORI = {"Grigio": "heather grey", "Blu": "navy blue", "Rosso": "bordeaux red", "Nero": "black", "Rosa": "blush pink",
          "Bianco": "white", "Salvia": "sage green", "Crema": "cream"}


def foto_da_fare():
    """(file, prompt) per ogni foto: una per prodotto, una per colore dove il soggetto dipende dal colore."""
    out = []
    for nome, varianti, _ in PRODOTTI:
        soggetto = SOGGETTI[nome]
        if "{colore}" in soggetto:
            for v in varianti:
                out.append((nome_foto(nome, v), soggetto.format(colore=COLORI[v])))
        else:
            out.append((nome_foto(nome), soggetto))
    return out


def leggi_env(percorso):
    for riga in Path(percorso).read_text(encoding="utf-8").splitlines():
        if "=" in riga and not riga.lstrip().startswith("#"):
            k, v = riga.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


stampa = threading.Lock()


def genera_una(file, soggetto, chiave, modello):
    from PIL import Image
    corpo = {"model": modello, "prompt": f"Subject: {soggetto}.\n\n{STILE}", "size": "1024x1024", "quality": "medium", "n": 1}
    for tentativo in range(4):  # limiti di frequenza ed errori temporanei: si riprova dopo una pausa
        req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=json.dumps(corpo).encode(),
                                     headers={"Authorization": f"Bearer {chiave}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                dati = json.load(r)
            break
        except urllib.error.HTTPError as e:
            errore = e.read().decode("utf-8", "ignore")
            with stampa:
                print(f"  ! {file}: {e.code} {errore[:200]}", flush=True)
            if "insufficient_quota" in errore or "billing" in errore:
                return "credito"
            if e.code not in (429, 500, 502, 503, 504):
                return "errore"
            time.sleep(20 * (tentativo + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            with stampa:
                print(f"  ! {file}: {e}", flush=True)
            time.sleep(20)
    else:
        return "errore"
    png = ORIGINALI / f"{file}.png"
    png.write_bytes(base64.b64decode(dati["data"][0]["b64_json"]))
    Image.open(png).convert("RGB").resize((512, 512), Image.LANCZOS).save(
        USCITA / f"{file}.jpg", "JPEG", quality=84, optimize=True, progressive=True)
    with stampa:
        print(f"  ok {file}.jpg", flush=True)
    return "ok"


def main():
    args = sys.argv[1:]
    percorso_env = args[args.index("--env") + 1] if "--env" in args else None
    scelti = [a for a in args if not a.startswith("--") and a != percorso_env]
    lista = foto_da_fare()
    if "--elenco" in args or "--genera" not in args:
        for file, soggetto in lista:
            print(f"{file}.jpg: {soggetto}")
        print(f"\n{len(lista)} foto")
        return
    if percorso_env:
        leggi_env(percorso_env)
    chiave = os.environ.get("OPENAI_API_KEY")
    if not chiave:
        sys.exit("Manca OPENAI_API_KEY (nell'ambiente o nel file indicato con --env).")
    modello = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
    USCITA.mkdir(parents=True, exist_ok=True)
    ORIGINALI.mkdir(parents=True, exist_ok=True)
    if scelti:
        da_fare = [(f, s) for f, s in lista if f in scelti]
    else:
        da_fare = [(f, s) for f, s in lista if not (USCITA / f"{f}.jpg").exists()]
    print(f"{len(da_fare)} foto da generare con {modello}", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        esiti = list(ex.map(lambda x: genera_una(x[0], x[1], chiave, modello), da_fare))
    print(f"\nfatte {esiti.count('ok')}, non riuscite {len(esiti) - esiti.count('ok')}")
    if "credito" in esiti:
        sys.exit("Credito OpenAI esaurito: ricaricare e rilanciare (le foto già fatte restano).")


if __name__ == "__main__":
    main()
