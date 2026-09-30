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

function addMessage(kind, text) {
  document.body.classList.add("started");
  const div = el("div", `msg ${kind}`, text);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderTools(container, outils) {
  const row = el("div", "tools");
  row.appendChild(el("span", null, "outils appelés :"));
  if (outils.length === 0) {
    row.appendChild(el("span", null, "aucun"));
  } else {
    outils.forEach((name) => row.appendChild(el("span", "chip", name)));
  }
  container.appendChild(row);
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
      details.appendChild(el("p", "answer", item.reponse));
      renderTools(details, item.outils);
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
    pending.textContent = data.reponse;
    renderTools(pending, data.outils);
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
