// Magazzino: piccoli aiuti nel browser. Tutto facoltativo: senza JavaScript le pagine funzionano lo stesso
// (il server fa ogni controllo). Nessuna libreria, nessuna richiesta esterna.
(function () {
  "use strict";
  var $ = function (sel, el) { return (el || document).querySelector(sel); };
  var $$ = function (sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); };
  function memoria(chiave, valore) {             // localStorage può mancare o essere bloccato
    try {
      if (valore === undefined) return window.localStorage.getItem(chiave);
      window.localStorage.setItem(chiave, valore);
    } catch (e) { return null; }
  }

  $$("[data-solo-js]").forEach(function (el) { el.hidden = false; });

  // ------------------------------------------------------------------ 1. doppio clic e conferme
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.hasAttribute("data-ajax")) return;
    var b = e.submitter || f.querySelector("button:not([type=button])");
    var testo = (b && b.getAttribute("data-conferma")) || f.getAttribute("data-conferma");
    if (testo && !window.confirm(testo)) { e.preventDefault(); return; }
    if (f.dataset.inviato) { e.preventDefault(); return; }
    f.dataset.inviato = "1";
    modificheNonSalvate = false;
    if (b) { b.setAttribute("aria-busy", "true"); b.dataset.testo = b.textContent; b.textContent = "Attendere…"; }
    // se la pagina non cambia (download, errore di rete) si sblocca dopo qualche secondo
    setTimeout(function () {
      delete f.dataset.inviato;
      if (b && b.dataset.testo) { b.removeAttribute("aria-busy"); b.textContent = b.dataset.testo; }
    }, 8000);
  }, true);

  // ------------------------------------------------------------------ 2. quantità con − e +
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".passo-qta > button[data-passo]");
    if (!b) return;
    var i = b.parentNode.querySelector("input");
    var v = (parseInt(i.value, 10) || 0) + parseInt(b.dataset.passo, 10);
    var min = i.min === "" ? -Infinity : +i.min, max = i.max === "" ? Infinity : +i.max;
    var nuovo = Math.max(min, Math.min(max, v));
    if (String(nuovo) === i.value) return;
    i.value = nuovo;
    i.dispatchEvent(new Event("input", { bubbles: true }));
    i.dispatchEvent(new Event("change", { bubbles: true }));
  });

  // ------------------------------------------------------------------ 3. suoni (generati, niente file)
  var audio = null;
  function suona(tipo) {
    var sw = $("[data-suono]");
    if (sw && !sw.checked) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var note = { ok: [[880, 0.07]], completa: [[988, 0.07], [1319, 0.09]], tutto: [[659, 0.08], [880, 0.08], [1319, 0.14]],
                   errore: [[220, 0.14], [185, 0.2]] }[tipo] || [[660, 0.08]];
      var t = audio.currentTime;
      note.forEach(function (n) {
        var o = audio.createOscillator(), g = audio.createGain();
        o.type = tipo === "errore" ? "square" : "sine"; o.frequency.value = n[0];
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + n[1]);
        o.connect(g); g.connect(audio.destination); o.start(t); o.stop(t + n[1] + 0.02); t += n[1] + 0.03;
      });
    } catch (e) { /* niente audio: pazienza */ }
    if (tipo === "errore" && navigator.vibrate) navigator.vibrate([80, 60, 80]);
  }
  var sw = $("[data-suono]");
  if (sw) {
    sw.checked = memoria("suono") !== "no";
    sw.addEventListener("change", function () { memoria("suono", sw.checked ? "si" : "no"); });
  }

  // ------------------------------------------------------------------ 4. il lettore di codici a barre
  // Il lettore «scrive» il codice e preme Invio. Dove la pagina ha un campo lettore [data-scanner] vince quello;
  // altrove il codice va nella ricerca in alto. Se il cursore è in un campo quantità, un codice lungo non
  // finisce lì dentro: viene girato al lettore (nessuna quantità ha 6 cifre).
  var lettore = $("[data-scanner]") || $("[data-lettore-globale]");
  var buffer = "", ultimo = 0;
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (e.key === "Enter" && t && t.matches && t.matches("input.qta") && t.value.length >= 6 && $("[data-scanner]")) {
      var s = $("[data-scanner]");
      e.preventDefault(); s.value = t.value; t.value = t.defaultValue; invia(s);
      return;
    }
    if (!lettore || (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT"))) return;
    var adesso = Date.now();
    if (adesso - ultimo > 80) buffer = "";
    ultimo = adesso;
    if (e.key === "Enter" && buffer.length >= 4) {
      lettore.value = buffer; buffer = ""; e.preventDefault(); invia(lettore);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      buffer += e.key;
    }
  });
  function invia(campo) {
    var f = campo.form || campo.closest("[data-lettore]");
    if (f && f.matches("[data-lettore=conta]")) { contaLettura(campo.value); campo.value = ""; return; }
    if (f && f.requestSubmit) f.requestSubmit(); else if (f && f.submit) f.submit();
  }
  if ($("[data-scanner]") && location.hash === "#scanner") $("[data-scanner]").focus();

  // ------------------------------------------------------------------ 5. preparazione: letture senza ricaricare
  var scheda = $("form[data-lettore=prelievo]");
  var coda = [], lavorando = false, ultimaLettura = null, modificheNonSalvate = false;
  function esito(testo, errore) {
    var box = $(".esito-lettura", scheda);
    if (!box) return;
    box.hidden = false;
    box.classList.toggle("errore", !!errore);
    box.setAttribute("role", errore ? "alert" : "status");
    $("[data-esito-testo]", box).textContent = testo;
    var icona = box.querySelector("svg");
    if (icona) icona.innerHTML = errore
      ? '<path d="M3.25 12a8.75 8.75 0 1 0 17.5 0a8.75 8.75 0 1 0 -17.5 0z"/><path d="M12 7.75v5"/><path d="M12 16.25h.01"/>'
      : '<path d="M4.75 12.5 9.5 17.25 19.25 7.25"/>';
    box.style.animation = "none"; void box.offsetWidth; box.style.animation = "";
  }
  function aggiornaTotali(d) {
    if (d.versione) $$("input[name=versione]").forEach(function (i) { i.value = d.versione; });
    if (d.approvati_totale === undefined) return;
    var testo = $("[data-avanz-testo]"), barra = $("[data-avanz-barra]");
    if (testo) testo.textContent = "Presi " + d.prelevati_totale + " di " + d.approvati_totale + " pezzi";
    if (barra) { barra.max = d.approvati_totale; barra.value = d.prelevati_totale; }
    var sp = $("[data-spedisci]"), motivo = $("[data-motivo]");
    if (sp && !sp.getAttribute("aria-busy")) {
      var mancano = d.approvati_totale - d.prelevati_totale;
      sp.textContent = d.tutto ? sp.dataset.testoTutto : sp.dataset.testoParziale.replace("{n}", mancano);
      sp.classList.toggle("secondario", !d.tutto);
      if (!sp.title || sp.title === "nessun pezzo preso") { sp.disabled = !d.prelevati_totale; sp.title = d.prelevati_totale ? "" : "nessun pezzo preso"; }
    }
    if (motivo && d.tutto) motivo.hidden = true;
  }
  function aggiornaRiga(d, evidenzia) {
    if (!d.riga) return;
    var tr = document.getElementById("riga-" + d.riga);
    if (!tr) return;
    var i = tr.querySelector("input.qta");
    if (i && document.activeElement !== i) { i.value = d.prelevata; i.defaultValue = d.prelevata; }
    tr.classList.toggle("completa", !!d.completa);
    if (evidenzia) {
      $$("tr.ultima").forEach(function (x) { x.classList.remove("ultima"); });
      tr.classList.add("ultima");
      tr.scrollIntoView({ block: "nearest" });
    }
  }
  function chiama(url, dati) {
    return fetch(url, { method: "POST", body: dati, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) {
        var tipo = r.headers.get("content-type") || "";
        if (tipo.indexOf("application/json") < 0) throw { sessione: true };
        return r.json();
      });
  }
  function nuovoId() {
    return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
  // ogni lettura ha il suo identificativo: se la rete cade si ripete la STESSA lettura e il server non la conta due volte
  function prossima() {
    if (lavorando || !coda.length) return;
    lavorando = true;
    var voce = coda[0], dati = new FormData();
    dati.append("gettone", scheda.querySelector("input[name=gettone]").value);
    dati.append("codice", voce.codice);
    dati.append("id_lettura", voce.id);
    chiama(scheda.action, dati).then(function (d) {
      coda.shift();
      aggiornaTotali(d);
      if (d.ok) {
        aggiornaRiga(d, true);
        esito(d.messaggio + (d.ubicazione ? " · scaffale " + d.ubicazione : ""), false);
        if (d.riga) {
          ultimaLettura = { riga: d.riga, prima: d.prelevata - 1 };
          var annulla = $("[data-annulla-ultima]"); if (annulla) annulla.hidden = false;
        }
        suona(d.tutto ? "tutto" : d.completa ? "completa" : "ok");
      } else {
        esito(d.messaggio, true);
        (d.righe || []).forEach(function (id) { var tr = document.getElementById("riga-" + id); if (tr) tr.scrollIntoView({ block: "nearest" }); });
        suona("errore");
      }
      lavorando = false; prossima();
    }).catch(function (err) {
      lavorando = false;
      if (err && err.sessione) {
        coda.length = 0; esito("Sessione scaduta: ricarica la pagina", true); suona("errore"); return;
      }
      esito("Connessione persa: riprovo… (" + coda.length + " in attesa)", true); suona("errore");
      setTimeout(prossima, 2500);
    });
  }
  if (scheda && window.fetch && window.FormData) {
    scheda.setAttribute("data-ajax", "");
    scheda.addEventListener("submit", function (e) {
      e.preventDefault();
      var campo = scheda.querySelector("[data-scanner]");
      var codice = (campo.value || "").trim();
      campo.value = ""; campo.focus();
      if (codice) { coda.push({ codice: codice, id: nuovoId() }); prossima(); }
    });
    var annulla = $("[data-annulla-ultima]");
    if (annulla) annulla.addEventListener("click", function () {
      if (!ultimaLettura) return;
      var dati = new FormData();
      dati.append("gettone", scheda.querySelector("input[name=gettone]").value);
      dati.append("p_" + ultimaLettura.riga, Math.max(0, ultimaLettura.prima));
      chiama(scheda.dataset.urlPreleva, dati).then(function (d) {
        aggiornaTotali(d); aggiornaRiga(d, true);
        esito(d.ok ? "Ultima lettura annullata: " + d.prodotto + " " + d.prelevata + " di " + d.approvata : d.messaggio, !d.ok);
        ultimaLettura = null; annulla.hidden = true;
        $("[data-scanner]").focus();
      }).catch(function () { esito("Non annullato: connessione persa, riprova", true); });
    });
    // i numeri scritti a mano si salvano da soli, riga per riga
    $$("#prelievo input.qta").forEach(function (i) {
      i.addEventListener("input", function () { modificheNonSalvate = true; });
      i.addEventListener("change", function () {
        var dati = new FormData();
        dati.append("gettone", scheda.querySelector("input[name=gettone]").value);
        dati.append(i.name, i.value);
        var tr = i.closest("tr");
        chiama(scheda.dataset.urlPreleva, dati).then(function (d) {
          aggiornaTotali(d);
          if (d.ok) { aggiornaRiga(d, false); i.defaultValue = i.value; tr.classList.remove("errore-riga"); modificheNonSalvate = false; }
          else { tr.classList.add("errore-riga"); esito(d.messaggio, true); suona("errore"); }
        }).catch(function () { esito("Numero non salvato: connessione persa. Premi «Salva i numeri».", true); });
      });
    });
    // il motivo di una spedizione incompleta si chiede solo quando si spedisce davvero incompleto
    var motivo = $("[data-motivo]"), spedisci = $("[data-spedisci]");
    if (motivo && !$("input", motivo).value) motivo.hidden = true;
    if (spedisci && motivo) spedisci.addEventListener("click", function (e) {
      if (spedisci.classList.contains("secondario") && !$("input", motivo).value.trim()) {
        e.preventDefault();
        motivo.hidden = false; $("input", motivo).focus();
        esito("Mancano pezzi: scrivi perché qui sotto, poi premi di nuovo «" + spedisci.textContent.trim() + "»", true);
      }
    });
    window.addEventListener("beforeunload", function (e) {
      if (modificheNonSalvate || coda.length) { e.preventDefault(); e.returnValue = ""; }
    });
  }

  // ------------------------------------------------------------------ 6. ricezione: conta con il lettore, rotti
  var ricezione = $("#ricezione"), conta = $("[data-lettore=conta]");
  function residuo(tr) { return parseInt(tr.dataset.residuo, 10) || 0; }
  function valore(tr, p) { var i = tr.querySelector('input[name^="' + p + '"]'); return i ? parseInt(i.value, 10) || 0 : 0; }
  function imposta(tr, p, v) { var i = tr.querySelector('input[name^="' + p + '"]'); if (i) i.value = v; }
  if (ricezione) {
    ricezione.addEventListener("change", function (e) {
      var i = e.target;
      if (!i.name || i.name.indexOf("d_") !== 0) return;
      var tr = i.closest("tr"), r = residuo(tr);
      if (valore(tr, "b_") + valore(tr, "d_") > r) imposta(tr, "b_", Math.max(0, r - valore(tr, "d_")));
    });
    var bottone = $("[data-conta-lettore]");
    if (bottone && conta) bottone.addEventListener("click", function () {
      conta.hidden = false; bottone.hidden = true;
      $$("tr[data-residuo]", ricezione).forEach(function (tr) { if (residuo(tr)) imposta(tr, "b_", 0); });
      lettore = $("[data-scanner]", conta); lettore.focus();
      esitoConta("Conta attiva: i buoni partono da zero, ogni lettura aggiunge 1", false);
    });
    var piu = $("[data-conta-aggiungi]");
    if (piu) piu.addEventListener("click", function () { var s = $("[data-scanner]", conta); contaLettura(s.value); s.value = ""; s.focus(); });
    if (conta) $("[data-scanner]", conta).addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); contaLettura(e.target.value); e.target.value = ""; }
    });
  }
  function esitoConta(testo, errore) {
    var box = $(".esito-lettura", conta);
    box.hidden = false; box.classList.toggle("errore", !!errore);
    $("[data-esito-testo]", box).textContent = testo;
  }
  function contaLettura(codice) {
    codice = (codice || "").trim();
    if (!codice || !ricezione) return;
    var righe = $$("tr[data-residuo]", ricezione).filter(function (tr) {
      return residuo(tr) > 0 && (tr.dataset.ean === codice || (tr.dataset.sku && tr.dataset.sku.toUpperCase() === codice.toUpperCase()));
    });
    if (!righe.length) { esitoConta("Non è in questo trasferimento: " + codice, true); suona("errore"); return; }
    var tr = righe.filter(function (x) { return valore(x, "b_") + valore(x, "d_") < residuo(x); })[0];
    if (!tr) { esitoConta("Basta così: di questo articolo sono già arrivati tutti", true); suona("errore"); return; }
    imposta(tr, "b_", valore(tr, "b_") + 1);
    $$("tr.ultima").forEach(function (x) { x.classList.remove("ultima"); });
    tr.classList.add("ultima"); tr.scrollIntoView({ block: "nearest" });
    var nome = tr.querySelector(".nome-prodotto");
    esitoConta((nome ? nome.textContent : codice) + ": " + valore(tr, "b_") + " di " + residuo(tr), false);
    suona(valore(tr, "b_") + valore(tr, "d_") >= residuo(tr) ? "completa" : "ok");
  }

  // ------------------------------------------------------------------ 7. approvazione: totale e righe oltre il libero
  var approva = $("#approvazione");
  if (approva) {
    var aggiorna = function () {
      var tot = 0;
      $$("tr[data-libero]", approva).forEach(function (tr) {
        var v = valore(tr, "q_"); tot += v;
        tr.classList.toggle("oltre", v > (parseInt(tr.dataset.libero, 10) || 0));
      });
      var b = $("[data-totale-approva]", approva);
      if (b && !b.getAttribute("aria-busy")) b.textContent = "Approva (" + tot + (tot === 1 ? " pezzo)" : " pezzi)");
    };
    approva.addEventListener("input", aggiorna); aggiorna();
  }

  // ------------------------------------------------------------------ 8. liste con spunta: tocco sulla riga, totali
  document.addEventListener("click", function (e) {
    var tr = e.target.closest && e.target.closest("table.dati tbody tr");
    if (!tr || e.target.closest("a, button, input, select, textarea, label")) return;
    var c = tr.querySelector('td > input[type="checkbox"]');
    if (c) { c.checked = !c.checked; c.dispatchEvent(new Event("change", { bubbles: true })); }
  });
  $$("[data-tutti]").forEach(function (box) {
    box.addEventListener("change", function () {
      box.closest("table").querySelectorAll("input[name=" + box.dataset.tutti + "]").forEach(function (c) { c.checked = box.checked; });
      box.dispatchEvent(new Event("aggiorna-conteggio", { bubbles: true }));
    });
  });
  $$("form[data-conta-selezione]").forEach(function (f) {
    var b = $("[data-crea]", f);
    var aggiorna = function () {
      var righe = 0, pezzi = 0;
      $$("input[name=chiave]:checked", f).forEach(function (c) { righe++; pezzi += parseInt(c.dataset.pezzi, 10) || 0; });
      if (b && !b.getAttribute("aria-busy")) {
        b.textContent = b.dataset.etichetta + " · " + righe + (righe === 1 ? " riga · " : " righe · ") + pezzi + (pezzi === 1 ? " pezzo" : " pezzi");
        b.disabled = righe === 0;
      }
    };
    f.addEventListener("change", aggiorna); f.addEventListener("aggiorna-conteggio", aggiorna); aggiorna();
  });

  // ------------------------------------------------------------------ 9. reintegro: periodo solo quando serve
  var modoReintegro = $$("input[name=modo][data-periodo]");
  if (modoReintegro.length) {
    var periodo = $("[data-campo-periodo]");
    var mostra = function () {
      var c = $("input[name=modo]:checked");
      if (periodo) periodo.hidden = !(c && c.value === "venduto");
    };
    modoReintegro.forEach(function (r) { r.addEventListener("change", mostra); }); mostra();
    // «Ultimi 7 · 14 · 30 giorni»: senza JavaScript il link calcola già; con JavaScript si tiene anche il resto del modulo
    $$("[data-giorni]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        var f = a.closest("form");
        if (!f) return;
        e.preventDefault();
        var fine = new Date(), inizio = new Date(); inizio.setDate(fine.getDate() - parseInt(a.dataset.giorni, 10));
        var iso = function (d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
        $("input[name=dal]", f).value = iso(inizio); $("input[name=al]", f).value = iso(fine);
        var v = $("input[name=modo][value=venduto]", f); if (v) v.checked = true;
        if (f.requestSubmit) f.requestSubmit(); else f.submit();
      });
    });
  }

  // ------------------------------------------------------------------ 10. movimenti Shopify: una scelta alla volta
  var carico = $("form[data-carico]");
  if (carico) {
    var rimostra = function () {
      var modo = ($("input[name=modo]:checked", carico) || {}).value;
      $$("[data-se-modo]", carico).forEach(function (el) {
        el.hidden = el.dataset.seModo.split(" ").indexOf(modo) < 0;
      });
    };
    rimostra();
    // cambiare scelta o regola ricalcola la tabella (il server ricalcola: stesso risultato che senza JS)
    carico.addEventListener("change", function (e) {
      if (!e.target.matches("input[name=modo], select[name=frazione], input[name=dispari]")) return;
      rimostra();
      var b = $("button[value=regola]", carico);
      if (b) { carico.action = carico.action.split("#")[0] + "#ripartizione"; b.click(); }
    });
    var ricalcola = $("button[value=regola]", carico);
    if (ricalcola) ricalcola.closest("[data-ricalcolo-manuale]") && (ricalcola.closest("[data-ricalcolo-manuale]").hidden = true);
  }
  var uscita = $("form[data-uscita]");
  if (uscita) {
    var aggiornaUscita = function () {
      var s = ($("input[name=scelta]:checked", uscita) || {}).value;
      $$("[data-se-negozio]", uscita).forEach(function (el) { el.hidden = !s || s === "uscita" || s === "gestito_fuori"; });
    };
    uscita.addEventListener("change", aggiornaUscita); aggiornaUscita();
  }

  // ------------------------------------------------------------------ 11. anomalie: selezione per ignorarle insieme
  var blocco = $("form[data-ignora-blocco]");
  if (blocco) {
    var aggiornaBlocco = function () {
      var n = $$("input[name=ids]:checked").length, b = $("[data-ignora-selezionate]");
      if (b && !b.getAttribute("aria-busy")) { b.textContent = "Ignora le selezionate (" + n + ")"; b.disabled = n === 0; }
    };
    document.addEventListener("change", function (e) { if (e.target.name === "ids") aggiornaBlocco(); });
    $$("[data-seleziona-tutte]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        $$(a.dataset.selezionaTutte + " input[name=ids]").forEach(function (c) { c.checked = true; });
        aggiornaBlocco();
      });
    });
    aggiornaBlocco();
  }

  // ------------------------------------------------------------------ 12. lettura dei negozi e scritture in corso
  var barra = $("[data-sync-in-corso]");
  if (barra) {
    var testo = document.getElementById("stato-sync");
    var giro = setInterval(function () {
      fetch("/stato-sync", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
        if (s.in_corso) { testo.textContent = "Lettura in corso: " + s.messaggio; }
        else { clearInterval(giro); location.reload(); }
      }).catch(function () { clearInterval(giro); });
    }, 2000);
  }
  var attesa = $("[data-ricarica]");
  if (attesa) {
    setTimeout(function () {
      var occupato = document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.value;
      if (!occupato && !coda.length) location.reload();
    }, parseInt(attesa.dataset.ricarica, 10) * 1000);
  }

  // telefono: la voce attiva del menu in basso resta in vista
  var attiva = $(".menu a.attivo");
  if (attiva && window.matchMedia && window.matchMedia("(max-width: 767.98px)").matches) attiva.scrollIntoView({ inline: "center", block: "nearest" });
})();
