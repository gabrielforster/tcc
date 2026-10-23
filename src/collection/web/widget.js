/**
 * Embeddable collection chat widget.
 *
 *   <script src="https://host/chat/widget.js" data-api="https://host" defer></script>
 *
 * One file, no build step and no dependencies. That is a deliberate constraint: this gets
 * pasted into someone else's page, and a widget that needs a bundler, a framework or a
 * version of React that disagrees with the host's is a widget that does not get embedded.
 *
 * Everything renders inside a shadow root, so the host page's CSS cannot reach in and the
 * widget's cannot leak out. Embedded widgets that skip this are the ones that look fine in
 * the demo and break on the first real site with a global `button { }` rule.
 *
 * Identity: `data-token` carries a token the host application minted on its own backend.
 * Without one the conversation is anonymous, which still answers anything in the knowledge
 * base. The widget never sees a secret and cannot mint a token itself.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var config = {
    api: (script && script.dataset.api) || "",
    token: (script && script.dataset.token) || null,
    title: (script && script.dataset.title) || "Atendimento",
    accent: (script && script.dataset.accent) || "#1f7a5c",
    open: script && script.dataset.open === "true",
  };
  config.api = config.api.replace(/\/+$/, "");

  var STRINGS = {
    placeholder: "Mensagem",
    send: "Enviar",
    opening: "Conectando...",
    offline: "Não consegui conectar agora. Tente novamente em instantes.",
    rateLimited: "Muitas mensagens seguidas. Aguarde alguns segundos.",
    expired: "Esta conversa expirou. Recarregue a página para começar outra.",
    handedOver: "Conversa transferida para uma pessoa da equipe.",
    automated: "Atendimento automático",
    today: "HOJE",
    record: "Gravar áudio",
    recording: "Gravando... solte para enviar",
    holdToRecord: "Segure o botão para gravar.",
    play: "Reproduzir áudio",
    pause: "Pausar áudio",
    audioRefused: "Não consegui ouvir esse áudio. Tente de novo ou escreva a sua mensagem.",
    noRecorder: "Gravação de áudio indisponível neste navegador. Escreva a sua mensagem.",
    micDenied: "Microfone bloqueado. Libere o acesso no navegador ou escreva a sua mensagem.",
    noMic: "Nenhum microfone encontrado. Escreva a sua mensagem.",
    micFailed: "Não consegui acessar o microfone. Escreva a sua mensagem.",
    confirmTitle: "Confirme o que entendemos",
    confirmYes: "Sim, é isso",
    confirmNo: "Não, vou corrigir",
    heard: "transcrição",
    confidence: "confiança",
    readBackRequired: "leitura de volta exigida",
  };

  // What each rung means to the customer. The tick is drawn from the status the server
  // last confirmed, never from a timer: a tick nobody reported would be a made-up receipt.
  var TICK_LABEL = {
    enviada: "Enviada",
    entregue: "Entregue",
    lida: "Lida",
    respondida: "Respondida",
  };
  var RANK = { enviada: 0, entregue: 1, lida: 2, respondida: 3 };

  var CONFIRM_MARKER = "Confirma que é isso?";
  var MAX_RECORD_MS = 110000;
  var MIN_RECORD_MS = 600;

  var TICK_ONE =
    '<path d="M1 5.5L3.6 8 8.6 2.2" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round" stroke-linejoin="round"/>';
  var TICK_TWO =
    '<path d="M6.4 8l5-5.8" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round" stroke-linejoin="round"/>';
  var MIC_ICON =
    '<svg width="16" height="22" viewBox="0 0 16 22" fill="none" aria-hidden="true">' +
    '<rect x="5" y="1" width="6" height="11" rx="3" fill="#fff"/>' +
    '<path d="M2 10a6 6 0 0012 0M8 16v5" stroke="#fff" stroke-width="1.8" ' +
    'stroke-linecap="round"/></svg>';
  var SEND_ICON =
    '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">' +
    '<path d="M2 10l16-7-6 15-2.5-6L2 10z" fill="#fff"/></svg>';
  var PLAY_ICON = '<svg width="10" height="12" viewBox="0 0 10 12" fill="none" ' +
    'aria-hidden="true"><path d="M1 1l8 5-8 5V1z" fill="#fff"/></svg>';
  var PAUSE_ICON = '<svg width="10" height="12" viewBox="0 0 10 12" fill="none" ' +
    'aria-hidden="true"><path d="M1 1h3v10H1zM6 1h3v10H6z" fill="#fff"/></svg>';
  var WAVE = [7, 14, 20, 11, 17, 22, 9, 15, 19, 12, 7, 16, 21, 10, 14, 8];

  var CSS = [
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}",
    ".launcher{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;",
    "border:0;cursor:pointer;background:var(--accent);color:#fff;font-size:24px;line-height:1;",
    "box-shadow:0 6px 24px rgba(0,0,0,.25);z-index:2147483000}",
    ".panel{position:fixed;right:20px;bottom:88px;width:390px;max-width:calc(100vw - 32px);",
    "height:640px;max-height:calc(100vh - 120px);display:none;flex-direction:column;",
    "background:#efede6;color:#16201c;border-radius:14px;overflow:hidden;z-index:2147483000;",
    "box-shadow:0 12px 40px rgba(0,0,0,.28)}",
    ".panel.open{display:flex}",
    ".head{background:#16423a;color:#fff;padding:12px 16px;display:flex;align-items:center;",
    "gap:12px;flex:none}",
    ".avatar{width:40px;height:40px;border-radius:50%;background:#2e8f6e;display:flex;",
    "align-items:center;justify-content:center;font-size:15px;font-weight:600;",
    "letter-spacing:.5px;flex:none}",
    ".who{flex:1;min-width:0}",
    ".head h2{margin:0;font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;",
    "text-overflow:ellipsis}",
    ".head p{margin:2px 0 0;font-size:12px;color:#a9cfc2}",
    ".badge{background:#2e8f6e;font-size:10px;font-weight:600;letter-spacing:.6px;",
    "padding:5px 8px;border-radius:6px;flex:none}",
    ".log{flex:1;overflow-y:auto;padding:16px 14px;display:flex;flex-direction:column;gap:10px}",
    ".chip{align-self:center;background:#dcd8cc;color:#4a4f49;font-size:11px;font-weight:600;",
    "letter-spacing:.5px;padding:5px 12px;border-radius:10px}",
    ".msg{max-width:84%;padding:10px 12px 7px;font-size:14px;line-height:1.45;",
    "white-space:pre-wrap;word-wrap:break-word}",
    ".msg.agent{background:#d4ede0;align-self:flex-end;border-radius:14px 14px 4px 14px}",
    ".msg.customer{background:#fff;align-self:flex-start;border-radius:14px 14px 14px 4px}",
    ".msg.system{align-self:center;background:#fdf2c7;border:1px solid #f0d98a;font-size:12px;",
    "border-radius:10px;padding:7px 12px;text-align:center}",
    ".meta{display:flex;align-items:center;justify-content:flex-end;gap:4px;margin-top:4px;",
    "font-size:11px;color:#5a6b63;white-space:normal}",
    ".tick{display:inline-flex;color:#7a8f85}",
    ".tick.entregue{color:#7a8f85}",
    ".tick.lida,.tick.respondida{color:#2e8f6e}",
    ".cite{display:block;margin-top:6px;font-size:11px;color:#4f6b5f;",
    "border-top:1px solid rgba(0,0,0,.08);padding-top:5px}",
    ".typing{align-self:flex-end;font-size:12px;color:#5a6b63}",
    ".heard{align-self:flex-start;display:flex;align-items:center;gap:6px;padding-left:4px;",
    "font-size:11px;color:#5a6b63}",
    ".confirm{background:#fff8e1;border:2px solid #b7791f;border-radius:10px;padding:10px 12px;",
    "margin:-2px -2px 8px;white-space:pre-wrap}",
    ".confirm strong{display:block;font-size:11px;letter-spacing:.6px;text-transform:uppercase;",
    "color:#7a4b00;margin-bottom:4px}",
    ".confirm .said{font-size:15px;font-weight:600;color:#16201c}",
    ".confirm .ask{display:flex;gap:8px;margin-top:10px}",
    ".confirm button{flex:1;min-height:40px;border-radius:8px;font-size:14px;font-weight:600;",
    "cursor:pointer;border:2px solid #1f7a5c}",
    ".confirm button.yes{background:#1f7a5c;color:#fff}",
    ".confirm button.no{background:#fff;color:#1f7a5c}",
    ".confirm button:disabled{opacity:.5;cursor:not-allowed}",
    ".audio{display:flex;align-items:center;gap:10px;min-width:210px}",
    ".audio button{width:32px;height:32px;min-width:32px;border-radius:50%;border:0;",
    "background:#1f7a5c;display:flex;align-items:center;justify-content:center;cursor:pointer}",
    ".wave{display:flex;align-items:center;gap:2px;flex:1;height:24px}",
    ".wave i{display:block;width:2px;background:#9fb3a9;border-radius:1px}",
    ".dur{font-size:12px;color:#5a6b63}",
    ".note{padding:6px 14px;font-size:12px;color:#5a4a1a;background:#fdf2c7;flex:none}",
    ".note[hidden]{display:none}",
    "form{display:flex;align-items:center;gap:8px;padding:8px 12px 16px;background:#efede6;",
    "flex:none}",
    "input{flex:1;min-width:0;padding:11px 16px;border:0;border-radius:22px;font-size:14px;",
    "background:#fff;color:#16201c}",
    "input:focus-visible{outline:2px solid #1f7a5c}",
    "input::placeholder{color:#5a6b63}",
    "form button{width:44px;height:44px;min-width:44px;border-radius:50%;border:0;",
    "background:#1f7a5c;display:flex;align-items:center;justify-content:center;cursor:pointer;",
    "touch-action:none;user-select:none;-webkit-user-select:none}",
    "form button:focus-visible,.audio button:focus-visible{outline:2px solid #16423a;",
    "outline-offset:2px}",
    "form button[hidden]{display:none}",
    "form button.recording{background:#c0392b}",
    "button:disabled,input:disabled{opacity:.55;cursor:not-allowed}",
    "@media (max-width:420px){.panel{right:8px;left:8px;width:auto;bottom:80px}}",
  ].join("");

  var host = document.createElement("div");
  host.setAttribute("data-collection-chat", "");
  var root = host.attachShadow({ mode: "open" });
  var style = document.createElement("style");
  style.textContent = CSS;
  root.appendChild(style);

  var wrap = document.createElement("div");
  wrap.style.setProperty("--accent", config.accent);
  wrap.innerHTML =
    '<button class="launcher" type="button" aria-label="Abrir atendimento">&#128172;</button>' +
    '<section class="panel" role="dialog" aria-label="' + escapeAttr(config.title) + '">' +
    '<div class="head"><div class="avatar" aria-hidden="true">' + escapeHtml(initials(config.title)) +
    '</div><div class="who"><h2>' + escapeHtml(config.title) + "</h2><p>" +
    STRINGS.automated + '</p></div><div class="badge" aria-hidden="true">CHAT</div></div>' +
    '<div class="log" aria-live="polite"></div>' +
    '<div class="note" role="status" hidden></div>' +
    '<form><input type="text" autocomplete="off" placeholder="' +
    escapeAttr(STRINGS.placeholder) + '" aria-label="' + escapeAttr(STRINGS.placeholder) +
    '" maxlength="1000" disabled>' +
    '<button class="mic" type="button" aria-label="' + escapeAttr(STRINGS.record) +
    '" disabled>' + MIC_ICON + "</button>" +
    '<button class="send" type="submit" aria-label="' + escapeAttr(STRINGS.send) +
    '" disabled>' + SEND_ICON + "</button></form></section>";
  root.appendChild(wrap);

  var launcher = wrap.querySelector(".launcher");
  var panel = wrap.querySelector(".panel");
  var log = wrap.querySelector(".log");
  var note = wrap.querySelector(".note");
  var form = wrap.querySelector("form");
  var input = wrap.querySelector("input");
  var sendButton = wrap.querySelector("button.send");
  var micButton = wrap.querySelector("button.mic");

  var sessionId = null;
  var opening = false;
  var closed = false;
  var busy = false;
  var micAvailable = true;
  var dateChipShown = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(value) {
    return escapeHtml(value);
  }
  function initials(title) {
    var words = String(title).split(/\s+/).filter(Boolean);
    var letters = (words[0] || "A").charAt(0) + (words.length > 1 ? words[1].charAt(0) : "");
    return letters.toUpperCase();
  }
  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }
  function clock() {
    var now = new Date();
    return pad(now.getHours()) + ":" + pad(now.getMinutes());
  }
  function mmss(seconds) {
    var whole = Math.max(0, Math.round(seconds));
    return Math.floor(whole / 60) + ":" + pad(whole % 60);
  }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.appendChild(document.createTextNode(text));
    return node;
  }

  function showNote(text) {
    note.textContent = text;
    note.hidden = false;
  }
  function clearNote() {
    note.hidden = true;
    note.textContent = "";
  }

  // ------------------------------------------------------------ delivery reporting
  // Nothing here guesses. A tick moves only when the server has answered a report, so it
  // says "the server was told", which is as much as this system can honestly claim.
  var inView = [];
  var observer = null;
  if (typeof IntersectionObserver === "function") {
    observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          var item = entry.target.__delivery;
          if (!item) return;
          var at = inView.indexOf(item);
          if (entry.isIntersecting && at < 0) inView.push(item);
          if (!entry.isIntersecting && at >= 0) inView.splice(at, 1);
        });
        reportRead();
      },
      { root: log, threshold: 0.6 }
    );
  }

  function drawTick(item) {
    var status = item.status;
    item.tick.className = "tick " + status;
    item.tick.setAttribute("role", "img");
    item.tick.setAttribute("aria-label", TICK_LABEL[status]);
    item.tick.setAttribute("title", TICK_LABEL[status]);
    item.tick.innerHTML =
      '<svg width="15" height="10" viewBox="0 0 15 10" fill="none" aria-hidden="true">' +
      TICK_ONE + (status === "enviada" ? "" : TICK_TWO) + "</svg>";
  }

  function report(item, status) {
    if (RANK[status] <= RANK[item.status] || !sessionId) return item.chain;
    // One report at a time per message, so `lida` cannot overtake `entregue` on the wire.
    item.chain = item.chain.then(function () {
      if (RANK[status] <= RANK[item.status]) return null;
      return request(
        "/chat/sessions/" + encodeURIComponent(sessionId) + "/deliveries/" +
          encodeURIComponent(item.id),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: status }),
        }
      ).then(
        function (body) {
          if (body && RANK[body.status] > RANK[item.status]) {
            item.status = body.status;
            drawTick(item);
          }
        },
        function () {
          // The report did not land, so the tick stays where the server last put it.
        }
      );
    });
    return item.chain;
  }

  function reportRead() {
    if (document.visibilityState === "hidden") return;
    inView.slice().forEach(function (item) {
      report(item, "lida");
    });
  }
  document.addEventListener("visibilitychange", reportRead);

  function track(bubble, deliveryId, metaRow) {
    var item = {
      id: deliveryId,
      status: "enviada",
      tick: el("span"),
      chain: Promise.resolve(),
    };
    metaRow.appendChild(item.tick);
    drawTick(item);
    bubble.__delivery = item;
    // Rendering is what `entregue` claims.
    report(item, "entregue");
    if (observer) observer.observe(bubble);
  }

  /**
   * Every message is inserted as a text node, never as HTML.
   *
   * The reply is generated text that quotes a knowledge base. Treating it as markup would
   * hand whoever can influence either one a script tag on the host's page.
   */
  function append(role, text, citations, deliveryId) {
    var bubble = el("div", "msg " + role);
    if (role === "system") {
      bubble.appendChild(document.createTextNode(text));
      push(bubble);
      return bubble;
    }
    if (!dateChipShown) {
      dateChipShown = true;
      log.appendChild(el("div", "chip", STRINGS.today));
    }
    var body = el("div");
    bubble.appendChild(body);
    body.appendChild(document.createTextNode(text));
    if (citations && citations.length) {
      bubble.appendChild(el("span", "cite", "Fonte: " + citations.join(", ")));
    }
    var meta = el("div", "meta");
    meta.appendChild(el("span", "", clock()));
    bubble.appendChild(meta);
    bubble.__body = body;
    bubble.__meta = meta;
    push(bubble);
    if (role === "agent" && deliveryId) track(bubble, deliveryId, meta);
    return bubble;
  }

  function push(bubble) {
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
  }

  /**
   * The read-back. When a spoken figure needs confirming, this is the thing the customer
   * must not scroll past: a bordered card at the top of the reply, with the two answers
   * one tap away. The text after the question mark is the ordinary answer.
   */
  function appendConfirmation(text, citations, deliveryId) {
    var cut = text.indexOf(CONFIRM_MARKER);
    var readBack = cut < 0 ? text : text.slice(0, cut + CONFIRM_MARKER.length);
    var rest = cut < 0 ? "" : text.slice(cut + CONFIRM_MARKER.length).replace(/^\s+/, "");
    var bubble = append("agent", rest, citations, deliveryId);
    if (!rest) bubble.__body.parentNode.removeChild(bubble.__body);

    var card = el("div", "confirm");
    card.setAttribute("role", "group");
    card.setAttribute("aria-label", STRINGS.confirmTitle);
    card.appendChild(el("strong", "", STRINGS.confirmTitle));
    card.appendChild(el("div", "said", readBack));
    var ask = el("div", "ask");
    var yes = el("button", "yes", STRINGS.confirmYes);
    var no = el("button", "no", STRINGS.confirmNo);
    yes.type = "button";
    no.type = "button";
    function answer(text) {
      yes.disabled = true;
      no.disabled = true;
      if (!busy && !closed) send(text);
    }
    yes.addEventListener("click", function () { answer("Sim, é isso."); });
    no.addEventListener("click", function () { answer("Não, não é isso."); });
    ask.appendChild(yes);
    ask.appendChild(no);
    card.appendChild(ask);
    bubble.insertBefore(card, bubble.firstChild);
    log.scrollTop = log.scrollHeight;
    return bubble;
  }

  function appendAudio(blobUrl, seconds) {
    var bubble = el("div", "msg customer");
    if (!dateChipShown) {
      dateChipShown = true;
      log.appendChild(el("div", "chip", STRINGS.today));
    }
    var row = el("div", "audio");
    var play = el("button");
    play.type = "button";
    play.setAttribute("aria-label", STRINGS.play);
    play.innerHTML = PLAY_ICON;
    var wave = el("div", "wave");
    wave.setAttribute("aria-hidden", "true");
    WAVE.forEach(function (h) {
      var bar = el("i");
      bar.style.height = h + "px";
      wave.appendChild(bar);
    });
    row.appendChild(play);
    row.appendChild(wave);
    row.appendChild(el("span", "dur", mmss(seconds)));
    bubble.appendChild(row);
    var meta = el("div", "meta");
    meta.appendChild(el("span", "", clock()));
    bubble.appendChild(meta);

    var player = null;
    play.addEventListener("click", function () {
      if (!player) {
        player = new Audio(blobUrl);
        player.addEventListener("ended", function () {
          play.innerHTML = PLAY_ICON;
          play.setAttribute("aria-label", STRINGS.play);
        });
      }
      if (player.paused) {
        player.play();
        play.innerHTML = PAUSE_ICON;
        play.setAttribute("aria-label", STRINGS.pause);
      } else {
        player.pause();
        play.innerHTML = PLAY_ICON;
        play.setAttribute("aria-label", STRINGS.play);
      }
    });
    bubble.__meta = meta;
    push(bubble);
    return bubble;
  }

  function appendHeard(body) {
    var parts = [STRINGS.heard];
    if (typeof body.transcription_confidence === "number" && body.transcript) {
      parts.push(STRINGS.confidence + " " + body.transcription_confidence.toFixed(2));
    }
    if (body.needs_confirmation) parts.push(STRINGS.readBackRequired);
    var line = el("div", "heard", parts.join(" · "));
    if (body.transcript) line.title = body.transcript;
    push(line);
  }

  // Like a messaging app: the mic until there is something to send, then the send button.
  // With no mic available the send button is always the one shown.
  function syncActions() {
    var typed = input.value.trim().length > 0;
    sendButton.hidden = micAvailable && !typed;
    micButton.hidden = !micAvailable || typed;
  }
  input.addEventListener("input", syncActions);

  function setEnabled(enabled) {
    busy = !enabled;
    input.disabled = !enabled;
    sendButton.disabled = !enabled;
    micButton.disabled = !enabled || !micAvailable;
  }

  function finish(message) {
    closed = true;
    setEnabled(false);
    if (message) append("system", message);
  }

  function request(path, options) {
    return fetch(config.api + path, options).then(function (response) {
      if (response.status === 204) return null;
      return response.json().then(
        function (body) {
          if (!response.ok) {
            var error = new Error((body && body.detail) || response.statusText);
            error.status = response.status;
            throw error;
          }
          return body;
        },
        function () {
          var error = new Error(response.statusText);
          error.status = response.status;
          throw error;
        }
      );
    });
  }

  function openSession() {
    if (sessionId || opening) return Promise.resolve();
    opening = true;
    append("system", STRINGS.opening);
    return request("/chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: config.token }),
    })
      .then(function (body) {
        log.innerHTML = "";
        dateChipShown = false;
        sessionId = body.session_id;
        append("agent", body.greeting, null, body.delivery_id);
        setEnabled(true);
        input.focus();
      })
      .catch(function () {
        log.innerHTML = "";
        append("system", STRINGS.offline);
      })
      .then(function () {
        opening = false;
      });
  }

  function showTyping() {
    var typing = el("div", "typing", "...");
    push(typing);
    return typing;
  }

  function showReply(body) {
    if (body.needs_confirmation) {
      appendConfirmation(body.text, body.citations, body.delivery_id);
    } else {
      append("agent", body.text, body.citations, body.delivery_id);
    }
    if (body.escalated) finish(STRINGS.handedOver);
    else if (body.opted_out) finish(null);
    else setEnabled(true);
    input.focus();
  }

  function showFailure(error, refused) {
    if (error.status === 429) {
      append("system", STRINGS.rateLimited);
      setEnabled(true);
    } else if (error.status === 409) {
      finish(STRINGS.handedOver);
    } else if (error.status === 404) {
      finish(STRINGS.expired);
    } else if (error.status === 422 && refused) {
      append("system", refused);
      setEnabled(true);
    } else {
      append("system", STRINGS.offline);
      setEnabled(true);
    }
  }

  function send(text) {
    append("customer", text);
    setEnabled(false);
    var typing = showTyping();

    request("/chat/sessions/" + encodeURIComponent(sessionId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then(function (body) {
        typing.remove();
        showReply(body);
      })
      .catch(function (error) {
        typing.remove();
        showFailure(error);
      });
  }

  function sendAudio(blob, seconds, extension) {
    var bubble = appendAudio(URL.createObjectURL(blob), seconds);
    setEnabled(false);
    var typing = showTyping();
    var data = new FormData();
    data.append("file", blob, "voz." + extension);

    request("/chat/sessions/" + encodeURIComponent(sessionId) + "/audio", {
      method: "POST",
      body: data,
    })
      .then(function (body) {
        typing.remove();
        appendHeard(body);
        showReply(body);
      })
      .catch(function (error) {
        typing.remove();
        showFailure(error, STRINGS.audioRefused);
      });
    return bubble;
  }

  // ------------------------------------------------------------------- recording
  // Hold to record. The mic is shown only when the browser can actually record, and is
  // removed, with the reason on screen, the moment it turns out it cannot.
  function disableMic(reason) {
    micAvailable = false;
    syncActions();
    showNote(reason);
  }

  function pickMime() {
    var candidates = [
      ["audio/ogg;codecs=opus", "ogg"],
      ["audio/webm;codecs=opus", "webm"],
      ["audio/webm", "webm"],
      ["audio/mp4", "m4a"],
    ];
    if (typeof MediaRecorder.isTypeSupported === "function") {
      for (var i = 0; i < candidates.length; i++) {
        if (MediaRecorder.isTypeSupported(candidates[i][0])) return candidates[i];
      }
    }
    return ["", "webm"];
  }

  var recording = null; // { stream, recorder, chunks, startedAt, timer, cancelled }
  var starting = false;
  var releasedEarly = false;

  function stopTracks(stream) {
    stream.getTracks().forEach(function (track) {
      track.stop();
    });
  }

  function startRecording() {
    if (!micAvailable || busy || closed || recording || starting || !sessionId) return;
    starting = true;
    releasedEarly = false;
    clearNote();
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (stream) {
        starting = false;
        if (releasedEarly) {
          // Let go while the permission prompt was open: nothing was recorded.
          stopTracks(stream);
          return;
        }
        var choice = pickMime();
        var recorder;
        try {
          recorder = choice[0] ? new MediaRecorder(stream, { mimeType: choice[0] }) : new MediaRecorder(stream);
        } catch (e) {
          stopTracks(stream);
          disableMic(STRINGS.micFailed);
          return;
        }
        var state = {
          stream: stream,
          recorder: recorder,
          chunks: [],
          startedAt: Date.now(),
          extension: choice[1],
          timer: null,
        };
        recording = state;
        recorder.addEventListener("dataavailable", function (event) {
          if (event.data && event.data.size) state.chunks.push(event.data);
        });
        recorder.addEventListener("stop", function () {
          stopTracks(stream);
          var elapsed = Date.now() - state.startedAt;
          if (state.cancelled) return;
          if (elapsed < MIN_RECORD_MS || !state.chunks.length) {
            showNote(STRINGS.holdToRecord);
            return;
          }
          var type = recorder.mimeType || choice[0] || "audio/webm";
          sendAudio(new Blob(state.chunks, { type: type }), elapsed / 1000, state.extension);
        });
        recorder.start();
        state.timer = setTimeout(stopRecording, MAX_RECORD_MS);
        micButton.classList.add("recording");
        input.placeholder = STRINGS.recording;
        input.disabled = true;
      })
      .catch(function (error) {
        starting = false;
        var name = (error && error.name) || "";
        if (name === "NotAllowedError" || name === "SecurityError") {
          disableMic(STRINGS.micDenied);
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          disableMic(STRINGS.noMic);
        } else {
          disableMic(STRINGS.micFailed);
        }
      });
  }

  function stopRecording() {
    releasedEarly = true;
    var state = recording;
    if (!state) return;
    recording = null;
    clearTimeout(state.timer);
    micButton.classList.remove("recording");
    input.placeholder = STRINGS.placeholder;
    if (!busy) input.disabled = false;
    if (state.recorder.state !== "inactive") state.recorder.stop();
  }

  function setupMic() {
    var supported =
      typeof window.MediaRecorder === "function" &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function";
    if (!supported) {
      disableMic(STRINGS.noRecorder);
      return;
    }
    micButton.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      if (micButton.setPointerCapture) {
        try { micButton.setPointerCapture(event.pointerId); } catch (e) { /* not capturable */ }
      }
      startRecording();
    });
    micButton.addEventListener("pointerup", stopRecording);
    micButton.addEventListener("pointercancel", stopRecording);
    micButton.addEventListener("contextmenu", function (event) {
      event.preventDefault();
    });
    micButton.addEventListener("keydown", function (event) {
      if ((event.key === " " || event.key === "Enter") && !event.repeat) {
        event.preventDefault();
        startRecording();
      }
    });
    micButton.addEventListener("keyup", function (event) {
      if (event.key === " " || event.key === "Enter") stopRecording();
    });
    micButton.addEventListener("blur", stopRecording);
  }
  setupMic();
  syncActions();

  launcher.addEventListener("click", function () {
    var isOpen = panel.classList.toggle("open");
    if (isOpen && !closed) openSession();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || !sessionId || closed) return;
    input.value = "";
    syncActions();
    send(text);
  });

  function mount() {
    document.body.appendChild(host);
    if (config.open) {
      panel.classList.add("open");
      openSession();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  window.collectionChat = {
    open: function () {
      panel.classList.add("open");
      if (!closed) openSession();
    },
    close: function () {
      panel.classList.remove("open");
    },
  };
})();
