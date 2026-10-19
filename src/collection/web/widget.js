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
    accent: (script && script.dataset.accent) || "#1d4ed8",
    open: script && script.dataset.open === "true",
  };
  config.api = config.api.replace(/\/+$/, "");

  var STRINGS = {
    placeholder: "Escreva a sua mensagem...",
    send: "Enviar",
    opening: "Conectando...",
    offline: "Não consegui conectar agora. Tente novamente em instantes.",
    rateLimited: "Muitas mensagens seguidas. Aguarde alguns segundos.",
    expired: "Esta conversa expirou. Recarregue a página para começar outra.",
    handedOver: "Conversa transferida para uma pessoa da equipe.",
    automated: "Atendimento automático",
  };

  var CSS = [
    ":host{all:initial}",
    "*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}",
    ".launcher{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;",
    "border:0;cursor:pointer;background:var(--accent);color:#fff;font-size:24px;line-height:1;",
    "box-shadow:0 6px 24px rgba(0,0,0,.25);z-index:2147483000}",
    ".panel{position:fixed;right:20px;bottom:88px;width:360px;max-width:calc(100vw - 32px);",
    "height:520px;max-height:calc(100vh - 120px);display:none;flex-direction:column;",
    "background:#fff;color:#111;border-radius:14px;overflow:hidden;z-index:2147483000;",
    "box-shadow:0 12px 40px rgba(0,0,0,.28)}",
    ".panel.open{display:flex}",
    ".head{background:var(--accent);color:#fff;padding:12px 14px}",
    ".head h2{margin:0;font-size:15px;font-weight:600}",
    ".head p{margin:2px 0 0;font-size:11px;opacity:.85}",
    ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;",
    "background:#f6f7f9}",
    ".msg{max-width:85%;padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.45;",
    "white-space:pre-wrap;word-wrap:break-word}",
    ".msg.agent{background:#fff;border:1px solid #e3e6ea;align-self:flex-start}",
    ".msg.customer{background:var(--accent);color:#fff;align-self:flex-end}",
    ".msg.system{align-self:center;background:#fdf2c7;border:1px solid #f0d98a;font-size:12px}",
    ".cite{display:block;margin-top:6px;font-size:11px;color:#667;border-top:1px solid #eee;",
    "padding-top:5px}",
    ".typing{align-self:flex-start;font-size:12px;color:#667}",
    "form{display:flex;gap:8px;padding:10px;border-top:1px solid #e3e6ea;background:#fff}",
    "input{flex:1;padding:9px 11px;border:1px solid #cfd4da;border-radius:8px;font-size:14px}",
    "input:focus{outline:2px solid var(--accent);outline-offset:-1px}",
    "button.send{padding:9px 14px;border:0;border-radius:8px;background:var(--accent);",
    "color:#fff;font-size:14px;cursor:pointer}",
    "button.send:disabled,input:disabled{opacity:.55;cursor:not-allowed}",
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
    '<div class="head"><h2>' + escapeHtml(config.title) + "</h2><p>" +
    STRINGS.automated + "</p></div>" +
    '<div class="log" aria-live="polite"></div>' +
    '<form><input type="text" autocomplete="off" placeholder="' +
    escapeAttr(STRINGS.placeholder) + '" aria-label="' + escapeAttr(STRINGS.placeholder) +
    '" maxlength="1000" disabled>' +
    '<button class="send" type="submit" disabled>' + STRINGS.send + "</button></form></section>";
  root.appendChild(wrap);

  var launcher = wrap.querySelector(".launcher");
  var panel = wrap.querySelector(".panel");
  var log = wrap.querySelector(".log");
  var form = wrap.querySelector("form");
  var input = wrap.querySelector("input");
  var sendButton = wrap.querySelector("button.send");

  var sessionId = null;
  var opening = false;
  var closed = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(value) {
    return escapeHtml(value);
  }

  /**
   * Every message is inserted as a text node, never as HTML.
   *
   * The reply is generated text that quotes a knowledge base. Treating it as markup would
   * hand whoever can influence either one a script tag on the host's page.
   */
  function append(role, text, citations) {
    var bubble = document.createElement("div");
    bubble.className = "msg " + role;
    bubble.appendChild(document.createTextNode(text));
    if (citations && citations.length) {
      var cite = document.createElement("span");
      cite.className = "cite";
      cite.appendChild(document.createTextNode("Fonte: " + citations.join(", ")));
      bubble.appendChild(cite);
    }
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
    return bubble;
  }

  function setEnabled(enabled) {
    input.disabled = !enabled;
    sendButton.disabled = !enabled;
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
        sessionId = body.session_id;
        append("agent", body.greeting);
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

  function send(text) {
    append("customer", text);
    setEnabled(false);
    var typing = document.createElement("div");
    typing.className = "typing";
    typing.textContent = "...";
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;

    request("/chat/sessions/" + encodeURIComponent(sessionId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then(function (body) {
        typing.remove();
        append("agent", body.text, body.citations);
        if (body.escalated) finish(STRINGS.handedOver);
        else if (body.opted_out) finish(null);
        else setEnabled(true);
        input.focus();
      })
      .catch(function (error) {
        typing.remove();
        if (error.status === 429) {
          append("system", STRINGS.rateLimited);
          setEnabled(true);
        } else if (error.status === 409) {
          finish(STRINGS.handedOver);
        } else if (error.status === 404) {
          finish(STRINGS.expired);
        } else {
          append("system", STRINGS.offline);
          setEnabled(true);
        }
      });
  }

  launcher.addEventListener("click", function () {
    var isOpen = panel.classList.toggle("open");
    if (isOpen && !closed) openSession();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text || !sessionId || closed) return;
    input.value = "";
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
