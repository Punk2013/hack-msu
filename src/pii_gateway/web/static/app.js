/**
 * PII Gateway — Presentation Deck & Interactive Showcase Controller
 * Zero-scroll, single-screen slide flow with live PII detection, masking, and restoration.
 */

(() => {
  "use strict";

  // Read Session Token injected into <meta name="pii-session">
  const metaToken = document.querySelector('meta[name="pii-session"]');
  const sessionToken = metaToken ? (metaToken.getAttribute("content") || "") : "";

  // Application State
  const state = {
    currentSlide: 1,
    totalSlides: 5,
    mode: "realistic", // "realistic" | "marker"
    initialText: "",
    lastRunText: "",
    lastRunMode: "",
    presentationData: null,
    isLoading: false,
    hasError: false,
    autoPlayTimer: null,
  };

  // DOM Elements
  const el = {
    stepNavBtns: document.querySelectorAll(".step-nav-btn"),
    slidePanels: document.querySelectorAll(".slide-panel"),
    slideDots: document.querySelectorAll(".slide-dot"),
    btnPrev: document.getElementById("btn-prev-slide"),
    btnNext: document.getElementById("btn-next-slide"),
    btnStep1Forward: document.getElementById("btn-step1-forward"),
    lblNextBtn: document.getElementById("lbl-next-btn"),
    lblStepCounter: document.getElementById("lbl-step-counter"),
    sourceEditor: document.getElementById("source-editor"),
    lblEditorMeta: document.getElementById("lbl-editor-meta"),
    btnResetText: document.getElementById("btn-reset-text"),
    btnGenerateCase: document.getElementById("btn-generate-case"),
    btnModeRealistic: document.getElementById("btn-mode-realistic"),
    btnModeMarker: document.getElementById("btn-mode-marker"),
    btnFullscreen: document.getElementById("btn-fullscreen"),
    btnAutoplay: document.getElementById("btn-autoplay"),
    lblAutoplay: document.getElementById("lbl-autoplay"),
    errorBannerWrap: document.getElementById("error-banner-wrap"),
    errorBannerText: document.getElementById("error-banner-text"),

    // Slide 2 elements
    s2Count: document.getElementById("s2-count"),
    s2Types: document.getElementById("s2-types"),
    s2Latency: document.getElementById("s2-latency"),
    s2HighlightedText: document.getElementById("s2-highlighted-text"),
    s2SpansList: document.getElementById("s2-spans-list"),

    // Slide 3 elements
    s3MaskedText: document.getElementById("s3-masked-text"),
    s3TableBody: document.getElementById("s3-table-body"),
    s3CanaryBadge: document.getElementById("s3-canary-badge"),

    // Slide 4 elements
    s4AgentText: document.getElementById("s4-agent-text"),
    s4ModelName: document.getElementById("s4-model-name"),

    // Slide 5 elements
    s5Status: document.getElementById("s5-status"),
    s5Fidelity: document.getElementById("s5-fidelity"),
    s5Latency: document.getElementById("s5-latency"),
    s5Unresolved: document.getElementById("s5-unresolved"),
    s5FakesPreview: document.getElementById("s5-fakes-preview"),
    s5RestoredPreview: document.getElementById("s5-restored-preview"),
    s5Ribbon: document.getElementById("s5-transformations-ribbon"),
  };

  // Fallback preset text
  const DEFAULT_PROMPT_DOC = `[ПРОМПТ ДЛЯ АВТОНОМНОГО АГЕНТА: ОБРАБОТКА ОБРАЩЕНИЯ И ПОДГОТОВКА ОТВЕТА]
Инструкция: Проанализируй данные клиента и сформируй проект ответа.

[ВХОДНОЙ ДОКУМЕНТ: КАРТОЧКА КЛИЕНТА ИЗ CRM]
ФИО: Соколов Алексей Дмитриевич
Email: alex.sokolov@corporate.org
Телефон: +7 916 123-45-67
Паспорт: 45 10 987654

Статус: Заявка одобрена. Алексею Дмитриевичу нужно предоставить оригиналы документов до 15.11.2026.
Поручитель: Морозова Елена Васильевна (+7 903 555-44-33).`;

  /**
   * Helper to build authenticated API headers
   */
  function getHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (sessionToken && !sessionToken.includes("__SESSION_TOKEN__")) {
      headers["Authorization"] = `Bearer ${sessionToken}`;
    }
    return headers;
  }

  /**
   * Initialize Presentation Deck
   */
  async function init() {
    setupEventListeners();
    await loadInitialData();
    updateEditorMeta();
    goToSlide(1);
  }

  /**
   * Load presets or default case from server
   */
  async function loadInitialData() {
    try {
      const res = await fetch("/api/cases", { headers: getHeaders() });
      if (res.ok) {
        const cases = await res.json();
        if (cases && cases.length > 0) {
          state.initialText = cases[0].content;
          el.sourceEditor.value = state.initialText;
          return;
        }
      }
    } catch (err) {
      console.warn("Could not load /api/cases, using fallback", err);
    }
    state.initialText = DEFAULT_PROMPT_DOC;
    el.sourceEditor.value = state.initialText;
  }

  /**
   * Event Listeners Registration
   */
  function setupEventListeners() {
    // Top Step Navigation Breadcrumbs
    el.stepNavBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const step = parseInt(btn.dataset.step, 10);
        // If error, do not allow jumping to steps 2..5
        if (state.hasError && step > 1) return;
        goToSlide(step);
      });
    });

    // Bottom Navigation Dots
    el.slideDots.forEach((dot) => {
      dot.addEventListener("click", () => {
        const step = parseInt(dot.dataset.step, 10);
        if (state.hasError && step > 1) return;
        goToSlide(step);
      });
    });

    // Prev / Next Buttons
    el.btnPrev.addEventListener("click", () => prevSlide());
    el.btnNext.addEventListener("click", () => nextSlide());
    if (el.btnStep1Forward) {
      el.btnStep1Forward.addEventListener("click", () => {
        if (!state.hasError) goToSlide(2);
      });
    }

    // Source Editor Text Change: immediately clear old stale outputs
    el.sourceEditor.addEventListener("input", () => {
      updateEditorMeta();
      clearError();
      clearSlideOutputs();
      state.presentationData = null;
    });

    // Reset Text
    el.btnResetText.addEventListener("click", () => {
      el.sourceEditor.value = state.initialText;
      updateEditorMeta();
      clearError();
      clearSlideOutputs();
      state.presentationData = null;
    });

    // Generate Dynamic Case
    el.btnGenerateCase.addEventListener("click", async () => {
      await generateNewCase();
    });

    // Mode Switcher
    el.btnModeRealistic.addEventListener("click", () => setMode("realistic"));
    el.btnModeMarker.addEventListener("click", () => setMode("marker"));

    // Fullscreen Toggle
    el.btnFullscreen.addEventListener("click", toggleFullscreen);

    // Autoplay Toggle
    el.btnAutoplay.addEventListener("click", toggleAutoplay);

    // Keyboard Shortcuts Navigation
    document.addEventListener("keydown", handleKeyDown);
  }

  /**
   * Update character and line counters
   */
  function updateEditorMeta() {
    const val = el.sourceEditor.value || "";
    const chars = val.length;
    const lines = val.split("\n").length;
    el.lblEditorMeta.textContent = `Символов: ${chars} | Строк: ${lines}`;
  }

  /**
   * Clear all outputs across slides 2..5 to avoid displaying stale data
   */
  function clearSlideOutputs() {
    // Slide 2
    if (el.s2Count) el.s2Count.textContent = "0";
    if (el.s2Types) el.s2Types.textContent = "—";
    if (el.s2Latency) el.s2Latency.textContent = "0.0 мс";
    if (el.s2HighlightedText) el.s2HighlightedText.innerHTML = "";
    if (el.s2SpansList) el.s2SpansList.innerHTML = "";

    // Slide 3
    if (el.s3MaskedText) el.s3MaskedText.innerHTML = "";
    if (el.s3TableBody) el.s3TableBody.innerHTML = "";
    if (el.s3CanaryBadge) el.s3CanaryBadge.textContent = "ОЖИДАНИЕ ОБРАБОТКИ";

    // Slide 4
    if (el.s4AgentText) el.s4AgentText.innerHTML = "";

    // Slide 5
    if (el.s5Status) el.s5Status.textContent = "STATUS: —";
    if (el.s5Fidelity) el.s5Fidelity.textContent = "—";
    if (el.s5Latency) el.s5Latency.textContent = "—";
    if (el.s5Unresolved) el.s5Unresolved.textContent = "—";
    if (el.s5FakesPreview) el.s5FakesPreview.innerHTML = "";
    if (el.s5RestoredPreview) el.s5RestoredPreview.innerHTML = "";
    if (el.s5Ribbon) el.s5Ribbon.innerHTML = "";
  }

  /**
   * Show visible error banner and block forward progression
   */
  function showError(msg) {
    state.hasError = true;
    clearSlideOutputs();
    state.presentationData = null;

    if (el.errorBannerWrap && el.errorBannerText) {
      el.errorBannerText.textContent = msg;
      el.errorBannerWrap.style.display = "block";
    }

    if (el.btnNext) el.btnNext.disabled = true;
    if (el.btnStep1Forward) el.btnStep1Forward.disabled = true;
    goToSlide(1);
  }

  /**
   * Clear error state and enable controls
   */
  function clearError() {
    state.hasError = false;
    if (el.errorBannerWrap) {
      el.errorBannerWrap.style.display = "none";
    }
    if (el.btnNext) el.btnNext.disabled = false;
    if (el.btnStep1Forward) el.btnStep1Forward.disabled = false;
  }

  /**
   * Switch Active Mode
   */
  function setMode(newMode) {
    if (state.mode === newMode) return;
    state.mode = newMode;
    el.btnModeRealistic.classList.toggle("active", newMode === "realistic");
    el.btnModeMarker.classList.toggle("active", newMode === "marker");

    state.presentationData = null;
    clearSlideOutputs();
    clearError();
    if (state.currentSlide > 1) {
      runPresentationPipeline();
    }
  }

  /**
   * Generate fresh realistic synthetic customer case
   */
  async function generateNewCase() {
    el.btnGenerateCase.classList.add("loading");
    clearError();
    clearSlideOutputs();
    try {
      const res = await fetch("/api/cases/generate", {
        method: "POST",
        headers: getHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        el.sourceEditor.value = data.content;
        updateEditorMeta();
        state.presentationData = null;

        if (state.currentSlide > 1) {
          await runPresentationPipeline();
        }
      }
    } catch (err) {
      console.error("Failed to generate synthetic case:", err);
    } finally {
      el.btnGenerateCase.classList.remove("loading");
    }
  }

  /**
   * Slide Navigation Controller
   */
  async function goToSlide(stepNumber) {
    if (stepNumber < 1 || stepNumber > state.totalSlides) return;
    if (state.hasError && stepNumber > 1) return;

    state.currentSlide = stepNumber;

    // Update panels visibility
    el.slidePanels.forEach((panel) => {
      const idx = parseInt(panel.dataset.slideIndex, 10);
      panel.classList.toggle("active", idx === stepNumber);
    });

    // Update top breadcrumbs
    el.stepNavBtns.forEach((btn) => {
      const idx = parseInt(btn.dataset.step, 10);
      btn.classList.toggle("active", idx === stepNumber);
      btn.classList.toggle("passed", idx < stepNumber);
    });

    // Update bottom dots
    el.slideDots.forEach((dot) => {
      const idx = parseInt(dot.dataset.step, 10);
      dot.classList.toggle("active", idx === stepNumber);
    });

    // Update bottom buttons and labels
    el.lblStepCounter.textContent = `Шаг ${stepNumber} из ${state.totalSlides}`;
    el.btnPrev.disabled = (stepNumber === 1);

    if (stepNumber === 1) {
      el.lblNextBtn.textContent = "Детекция ПДн ➔";
    } else if (stepNumber === 2) {
      el.lblNextBtn.textContent = "Маскирование & Vault ➔";
    } else if (stepNumber === 3) {
      el.lblNextBtn.textContent = "Пример ответа ➔";
    } else if (stepNumber === 4) {
      el.lblNextBtn.textContent = "Восстановление ➔";
    } else if (stepNumber === 5) {
      el.lblNextBtn.textContent = "В начало (Шаг 1) ↺";
    }

    // If entering steps 2..5, execute presentation pipeline if data is stale
    const currentText = el.sourceEditor.value || "";
    if (stepNumber >= 2) {
      if (!state.presentationData || state.lastRunText !== currentText || state.lastRunMode !== state.mode) {
        const ok = await runPresentationPipeline();
        if (!ok) {
          // Pipeline failed: revert to slide 1
          goToSlide(1);
        }
      }
    }
  }

  function nextSlide() {
    if (state.hasError) return;
    if (state.currentSlide < state.totalSlides) {
      goToSlide(state.currentSlide + 1);
    } else {
      goToSlide(1);
    }
  }

  function prevSlide() {
    if (state.currentSlide > 1) {
      goToSlide(state.currentSlide - 1);
    }
  }

  /**
   * Run 5-step pipeline via backend API
   */
  async function runPresentationPipeline() {
    if (state.isLoading) return false;
    state.isLoading = true;

    const textToProcess = el.sourceEditor.value || "";
    try {
      const res = await fetch("/api/presentation/run", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          text: textToProcess,
          mode: state.mode,
        }),
      });

      if (!res.ok) {
        let errDetail = "Обработка отклонена локальным движком (Fail-Closed).";
        try {
          const errData = await res.json();
          if (errData && errData.detail) errDetail = errData.detail;
        } catch (_) {}
        showError(`⚠️ Отказ шлюза: ${errDetail} Старые результаты очищены, переход заблокирован.`);
        return false;
      }

      const data = await res.json();
      state.presentationData = data;
      state.lastRunText = textToProcess;
      state.lastRunMode = state.mode;
      clearError();

      renderSlide2(data.step2);
      renderSlide3(data.step3);
      renderSlide4(data.step4);
      renderSlide5(data.step5);
      return true;
    } catch (err) {
      console.error("Pipeline run error:", err);
      showError("⚠️ Ошибка сети или соединения с сервером шлюза.");
      return false;
    } finally {
      state.isLoading = false;
    }
  }

  /**
   * Render Slide 2: Detection Spans & Stats
   */
  function renderSlide2(step2) {
    el.s2Count.textContent = step2.spans_count;
    el.s2Types.textContent = step2.types_detected.length > 0
      ? step2.types_detected.join(", ")
      : "НЕТ";
    el.s2Latency.textContent = `${step2.detect_latency_ms} мс`;
    el.s2HighlightedText.innerHTML = step2.highlighted_html;

    if (!step2.spans || step2.spans.length === 0) {
      el.s2SpansList.innerHTML = `<div class="span-meta">Персональные данные не обнаружены.</div>`;
      return;
    }

    el.s2SpansList.innerHTML = step2.spans.map((s) => {
      const typeLower = (s.type || "").toLowerCase();
      return `
        <div class="span-item-card">
          <div class="span-item-left">
            <span class="span-raw-text">${escapeHtml(s.raw_text)}</span>
            <span class="span-meta">Символы: [${s.start}..${s.end}] | Детектор: ${escapeHtml(s.detector || "NER")}</span>
          </div>
          <div class="span-item-right">
            <span class="pii-tag pii-${typeLower}">${escapeHtml(s.type)}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  /**
   * Render Slide 3: Masked Context & Vault Table
   */
  function renderSlide3(step3) {
    el.s3MaskedText.innerHTML = step3.masked_highlighted_html;

    if (el.s3CanaryBadge) {
      el.s3CanaryBadge.textContent = "✓ ВСЕ НАЙДЕННЫЕ СУЩНОСТИ ЗАМЕНЕНЫ";
      el.s3CanaryBadge.style.color = "#34d399";
      el.s3CanaryBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";
    }

    if (!step3.entities_table || step3.entities_table.length === 0) {
      el.s3TableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#94a3b8; padding: 20px;">Нет сущностей для отображения.</td></tr>`;
      return;
    }

    el.s3TableBody.innerHTML = step3.entities_table.map((row) => {
      const typeLower = (row.type || "").toLowerCase();
      const declsHtml = (row.declensions || []).map((d) => `<div>${escapeHtml(d)}</div>`).join("");
      return `
        <tr>
          <td><code style="color:#60a5fa; font-weight:700;">${escapeHtml(row.id)}</code></td>
          <td><span class="pii-tag pii-${typeLower}" style="font-size:11px; padding: 1px 6px;">${escapeHtml(row.type)}</span></td>
          <td style="font-family:var(--font-mono); font-weight:600; color:#e2e8f0;">${escapeHtml(row.original)}</td>
          <td style="font-family:var(--font-mono); font-weight:700; color:#fde047;">${escapeHtml(row.replacement)}</td>
          <td style="font-size:11.5px; color:#94a3b8;">${declsHtml}</td>
        </tr>
      `;
    }).join("");
  }

  /**
   * Render Slide 4: Autonomous Agent Answer Sample
   */
  function renderSlide4(step4) {
    el.s4AgentText.innerHTML = step4.agent_output_highlighted_html;
    if (step4.model_name) {
      el.s4ModelName.textContent = step4.model_name;
    }
  }

  /**
   * Render Slide 5: Restoration (Demasking) & Transformations Ribbon
   */
  function renderSlide5(step5) {
    el.s5Status.textContent = `STATUS: ${(step5.status || "COMPLETE").toUpperCase()}`;
    el.s5Fidelity.textContent = step5.is_complete ? "100.0% (exact)" : "Неполное";
    el.s5Latency.textContent = `${step5.restore_latency_ms} мс`;
    el.s5Unresolved.textContent = step5.unresolved_count === 0 ? "0 ОШИБОК" : `${step5.unresolved_count} НЕ СОВПАЛО`;

    if (state.presentationData && state.presentationData.step4) {
      el.s5FakesPreview.innerHTML = state.presentationData.step4.agent_output_highlighted_html;
    }
    el.s5RestoredPreview.innerHTML = step5.restored_highlighted_html;

    if (!step5.transformations || step5.transformations.length === 0) {
      el.s5Ribbon.innerHTML = `<span class="span-meta">Трансформации не производились.</span>`;
      return;
    }

    el.s5Ribbon.innerHTML = step5.transformations.map((t) => {
      return `
        <div class="transform-pill">
          <span class="trans-fake">${escapeHtml(t.fake)}</span>
          <span class="trans-arrow">➔</span>
          <span class="trans-orig">${escapeHtml(t.original)}</span>
        </div>
      `;
    }).join("");
  }

  /**
   * Keyboard shortcuts handler
   */
  function handleKeyDown(e) {
    const inTextarea = (document.activeElement === el.sourceEditor);

    if (inTextarea) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        el.sourceEditor.blur();
        if (!state.hasError) goToSlide(2);
      }
      return;
    }

    if (e.key === " " || e.key === "ArrowRight") {
      e.preventDefault();
      nextSlide();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      prevSlide();
    } else if (e.key >= "1" && e.key <= "5") {
      e.preventDefault();
      goToSlide(parseInt(e.key, 10));
    } else if (e.key.toLowerCase() === "f") {
      e.preventDefault();
      toggleFullscreen();
    }
  }

  /**
   * Autoplay timer
   */
  function toggleAutoplay() {
    if (state.autoPlayTimer) {
      clearInterval(state.autoPlayTimer);
      state.autoPlayTimer = null;
      el.btnAutoplay.classList.remove("active");
      el.lblAutoplay.textContent = "Авто-показ";
    } else {
      el.btnAutoplay.classList.add("active");
      el.lblAutoplay.textContent = "Пауза [4с]";
      state.autoPlayTimer = setInterval(() => {
        nextSlide();
      }, 4000);
    }
  }

  /**
   * Fullscreen handler
   */
  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else if (document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  }

  /**
   * Safe HTML escape
   */
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Bootstrap when DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
