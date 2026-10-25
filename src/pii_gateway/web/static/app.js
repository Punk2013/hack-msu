"use strict";
const byId = id => document.getElementById(id);
const token = document.querySelector('meta[name="pii-session"]').content;
const source = byId("source"), mode = byId("mode"), answer = byId("answer");
const example = source.value;
let processed = false;

function clearResults() {
  processed = false;
  for (const id of ["masked", "restored", "answer-result", "count"]) byId(id).textContent = "—";
  byId("spans").replaceChildren();
  byId("metrics").textContent = "Запустите обработку текста.";
  byId("roundtrip").textContent = "После обработки сравним восстановленный текст с исходником.";
  byId("answer-status").textContent = "";
  answer.value = "";
  answer.disabled = true;
  byId("restore").disabled = true;
  byId("error").hidden = true;
}
function busy(value) {
  for (const id of ["source", "mode", "process", "example"]) byId(id).disabled = value;
  answer.disabled = value || !processed;
  byId("restore").disabled = value || !processed;
}
function showError(message) {
  byId("error").textContent = message;
  byId("error").hidden = false;
}
async function api(path, text) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, {
      method: "POST", headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
      body: JSON.stringify({text, mode: mode.value}), signal: controller.signal
    });
    if (!response.ok) throw new Error(`Обработка не выполнена (HTTP ${response.status}). Результат не подменяется демонстрацией.`);
    return await response.json();
  } finally { clearTimeout(timeout); }
}
byId("process").addEventListener("click", async () => {
  clearResults();
  busy(true);
  byId("metrics").textContent = "Обработка на вашем компьютере…";
  try {
    const data = await api("/api/process", source.value);
    if (typeof data.masked_text !== "string" || typeof data.restored_text !== "string" ||
        !Array.isArray(data.spans) || typeof data.exact !== "boolean" ||
        !["complete", "partial"].includes(data.status)) throw new Error("Некорректный ответ локального сервера.");
    byId("masked").textContent = data.masked_text;
    byId("restored").textContent = data.restored_text;
    byId("metrics").textContent = `Найдено фрагментов: ${data.spans.length}. Время локальной обработки: ${data.elapsed_ms} мс.`;
    byId("count").textContent = String(data.spans.length);
    for (const span of data.spans) {
      const row = document.createElement("tr");
      for (const value of [span.type, span.raw_text, `[${span.start}, ${span.end})`, span.detector]) {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      }
      byId("spans").append(row);
    }
    byId("roundtrip").textContent = data.exact && data.status === "complete"
      ? "Восстановленный текст дословно совпал с исходным."
      : "Точное восстановление не подтверждено. Проверьте результат.";
    answer.value = data.masked_text;
    processed = true;
  } catch (error) {
    clearResults();
    showError(error.name === "AbortError" ? "Локальный сервер не ответил за 30 секунд." : error.message);
  } finally { busy(false); }
});
byId("restore").addEventListener("click", async () => {
  byId("answer-result").textContent = "—";
  byId("answer-status").textContent = "";
  byId("error").hidden = true;
  busy(true);
  try {
    const data = await api("/api/restore", answer.value);
    if (typeof data.restored_text !== "string" || !Array.isArray(data.unresolved) ||
        !["complete", "partial"].includes(data.status)) throw new Error("Некорректный ответ локального сервера.");
    byId("answer-result").textContent = data.restored_text;
    byId("answer-status").textContent = data.status === "complete"
      ? "Известные замены восстановлены. Проверьте текст перед использованием."
      : `Частичное восстановление: неизвестных или повреждённых маркеров — ${data.unresolved.length}.`;
  } catch (error) { showError(error.name === "AbortError" ? "Истекло время ожидания." : error.message); }
  finally { busy(false); }
});
source.addEventListener("input", clearResults);
mode.addEventListener("change", clearResults);
answer.addEventListener("input", () => {
  byId("answer-result").textContent = "—";
  byId("answer-status").textContent = "";
});
byId("example").addEventListener("click", () => { source.value = example; clearResults(); });
