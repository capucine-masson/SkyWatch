const messagesEl = document.getElementById("messages");
const form = document.getElementById("ask-form");
const questionEl = document.getElementById("question");
const askBtn = document.getElementById("ask-btn");
const villeEl = document.getElementById("ville");
const saveVilleBtn = document.getElementById("save-ville");
const villeStatus = document.getElementById("ville-status");
const historyEl = document.getElementById("history");

// Toutes les données affichées passent par textContent (jamais innerHTML).
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(url, options) {
  const res = await fetch(url, options);
  let data = null;
  try { data = await res.json(); } catch (_) { /* corps non JSON */ }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `Erreur ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

// Réponse structurée (« ## 🌙 Titre », « - puce », « Conseil : … ») -> sections lisibles.
// Tout passe par textContent ; sans balise « ## », on garde le texte brut.
function renderAnswer(text) {
  const clean = text.replace(/\*\*/g, "");
  if (!/^##\s/m.test(clean)) return el("div", "rich plain", clean);

  const root = el("div", "rich");
  let section = null;
  let list = null;
  const newSection = () => {
    section = el("section", "rich-sec");
    section.style.setProperty("--i", root.children.length);
    root.appendChild(section);
    list = null;
    return section;
  };

  clean.split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) return;

    if (line.startsWith("##")) {
      const title = line.replace(/^#+\s*/, "");
      const [first, ...rest] = title.split(/\s+/);
      const hasIcon = rest.length > 0 && !/^[\p{L}\p{N}]/u.test(first);
      const head = el("header", "rich-head");
      if (hasIcon) head.appendChild(el("span", "rich-icon", first));
      head.appendChild(el("h3", null, hasIcon ? rest.join(" ") : title));
      newSection().appendChild(head);
    } else if (/^conseil\s*:/i.test(line)) {
      const tip = el("div", "rich-tip");
      tip.appendChild(el("span", "rich-tip-label", "Conseil"));
      tip.appendChild(el("p", null, line.replace(/^conseil\s*:\s*/i, "")));
      root.appendChild(tip);
      section = null;
    } else if (/^[-•]\s+/.test(line)) {
      if (!section) newSection();
      if (!list) {
        list = el("ul", "rich-list");
        section.appendChild(list);
      }
      const item = el("li");
      const body = line.replace(/^[-•]\s+/, "");
      const cut = body.indexOf(" : ");
      if (cut > 0 && cut <= 34) {   // « Grande Ourse : nord-ouest… » -> libellé mis en valeur
        item.appendChild(el("strong", null, body.slice(0, cut)));
        item.appendChild(document.createTextNode(` : ${body.slice(cut + 3)}`));
      } else {
        item.textContent = body;
      }
      list.appendChild(item);
    } else {
      root.appendChild(el("p", "rich-p", line));
    }
  });
  return root;
}

function addMessage(kind, text) {
  document.body.classList.add("started");
  const div = el("div", `msg ${kind}`, text);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

async function loadHistory() {
  try {
    const items = await api("/api/history");
    historyEl.replaceChildren();
    if (items.length === 0) {
      historyEl.appendChild(el("li", "history-empty", "Aucune question pour l'instant."));
      return;
    }
    items.forEach((item) => {
      const li = el("li");
      const details = el("details");
      details.appendChild(el("summary", null, item.question));
      const answer = renderAnswer(item.reponse);
      answer.classList.add("answer");
      details.appendChild(answer);
      details.appendChild(el("span", "note when", item.created_at));
      li.appendChild(details);
      historyEl.appendChild(li);
    });
  } catch (err) {
    historyEl.replaceChildren(el("li", "history-empty", err.message));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionEl.value.trim();
  if (!question || askBtn.disabled) return;

  askBtn.disabled = true;
  questionEl.disabled = true;
  addMessage("user", question);
  questionEl.value = "";
  const pending = addMessage("bot pending", "SkyWatch consulte le ciel");

  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    pending.className = "msg bot";
    pending.replaceChildren(renderAnswer(data.reponse));
    loadHistory();
  } catch (err) {
    pending.className = "msg bot error";
    pending.textContent = err.message;
  } finally {
    askBtn.disabled = false;
    questionEl.disabled = false;
    questionEl.focus();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
});

saveVilleBtn.addEventListener("click", async () => {
  saveVilleBtn.disabled = true;
  try {
    const data = await api("/api/settings/ville", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ville: villeEl.value }),
    });
    villeEl.value = data.ville;
    villeStatus.textContent = data.ville ? "enregistrée ✓" : "ville effacée";
  } catch (err) {
    villeStatus.textContent = err.message;
  } finally {
    saveVilleBtn.disabled = false;
    setTimeout(() => { villeStatus.textContent = ""; }, 3000);
  }
});

villeEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveVilleBtn.click();
});

loadHistory();
