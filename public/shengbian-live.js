(() => {
  const pageRole = document.body.dataset.defaultRole;
  const params = new URLSearchParams(window.location.search);
  const demoUser = params.get("as") || {
    platform: "platform-admin",
    property: "property-pengyi",
    resident: "resident-li",
    worker: "worker-wang",
  }[pageRole];
  const selectedCommunity = params.get("communityId");
  let selectedArticleId = Number(params.get("articleId")) || null;
  const ALLOWED_ATTACHMENT_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime"]);
  let selectedTicketId = Number(params.get("ticketId")) || Number(localStorage.getItem("shengbian-last-ticket")) || null;
  let residentCarouselTimer = null;
  let residentHeroTimer = null;
  let residentLoadRetryScheduled = false;
  const statusClass = {
    new: "wait",
    processing: "handle",
    awaiting_vendor: "third",
    awaiting_confirmation: "done",
    resolved: "done",
    reopened: "bad",
  };
  const allowedTransitions = {
    new: ["processing"],
    processing: ["awaiting_vendor", "awaiting_confirmation"],
    awaiting_vendor: ["processing", "awaiting_confirmation"],
    reopened: ["processing"],
  };

  const liveStyle = document.createElement("style");
  liveStyle.textContent = `
    .live-banner { margin: 14px auto 0; width: min(1180px, calc(100% - 32px)); display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border: 1px solid #b9e66f; border-radius: 11px; background: #f3fae9; color: #234b39; font-size: 12px; }
    .live-banner b { color: #174f3c; }
    .live-dot { width: 8px; height: 8px; display: inline-block; border-radius: 50%; margin-right: 7px; background: #5fa63d; box-shadow: 0 0 0 4px rgba(95,166,61,.13); }
    .live-panel { margin-top: 14px; padding: 15px; border: 1px solid #d9e7d4; border-radius: 13px; background: #fbfdf8; color: #234b39; }
    .live-panel h3 { margin: 0 0 8px; font-size: 14px; }
    .live-panel p { margin: 0 0 11px; font-size: 11px; line-height: 1.6; color: #52705f; }
    .live-panel select { width: 100%; margin: 0 0 9px; padding: 9px; border: 1px solid #c9dbc5; border-radius: 8px; background: #fff; color: #234b39; }
    .live-panel-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .live-panel button { cursor: pointer; padding: 8px 10px; border: 0; border-radius: 8px; background: #174f3c; color: #fff; font: inherit; font-size: 11px; }
    .live-panel button.subtle { background: #e7f1e1; color: #234b39; }
    .live-panel .live-readonly { color: #8a6340; font-weight: 700; }
    .live-project-button { padding: 0; border: 0; background: transparent; color: #267453; cursor: pointer; font: inherit; font-weight: 700; }
    .live-review-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid #e6eee2; }
    .live-review-row:first-of-type { border-top: 0; }
    .live-review-row small { display: block; margin-top: 3px; color: #718a7e; }
    .live-ticket-note { margin-top: 8px; color: #52705f; font-size: 10px; }
    .live-attachments { display: grid; gap: 5px; margin-top: 7px; }
    .live-attachment-link { padding: 0; color: #267453; font: inherit; font-size: 10px; font-weight: 700; text-align: left; background: transparent; border: 0; cursor: pointer; }
    .live-attachment-link:hover { text-decoration: underline; }
    .resident-ticket-list { display: grid; gap: 6px; margin: 13px 0 0; }
    .resident-ticket-list h4 { margin: 0 0 1px; color: #385f4c; font-size: 10px; }
    .resident-ticket-button { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; padding: 9px; color: #385f4c; text-align: left; background: #f5faf4; border: 1px solid #dcebdc; border-radius: 9px; font: inherit; cursor: pointer; }
    .resident-ticket-button.active { border-color: #78a979; background: #e8f4e6; box-shadow: inset 3px 0 #347657; }
    .resident-ticket-button b, .resident-ticket-button small { display: block; }
    .resident-ticket-button b { font-size: 10px; }
    .resident-ticket-button small { margin-top: 2px; color: #789486; font-size: 8px; }
    .resident-ticket-button .status { flex: 0 0 auto; }
    .resident-ticket-attachments { margin-top: 12px; }
    .resident-ticket-attachments h4 { margin: 0 0 6px; color: #385f4c; font-size: 10px; }
    .resident-carousel, .resident-content-feed { margin-top: 12px; }
    .resident-hero-carousel { min-height: 151px; padding: 0; background: #174c3a; }
    .resident-hero-slide { position: relative; display: flex; min-height: 151px; overflow: hidden; padding: 16px; color: #eff8f1; background: linear-gradient(145deg,#28674b,#174c3a); border: 0; border-radius: 16px; text-align: left; cursor: pointer; }
    .resident-hero-slide.has-image { background-position: center; background-size: cover; }
    .resident-hero-slide::after { position: absolute; inset: 0; content: ""; background: linear-gradient(90deg, rgba(8, 42, 31, .78), rgba(8, 42, 31, .12)); }
    .resident-hero-slide-copy { position: relative; z-index: 1; display: flex; flex-direction: column; align-self: flex-end; max-width: 220px; }
    .resident-hero-slide-copy small { color: #c6ddcd; font-size: 9px; font-weight: 800; }
    .resident-hero-slide-copy strong { display: block; margin: 8px 0 5px; color: #fff; font-size: 17px; line-height: 1.3; letter-spacing: -.04em; }
    .resident-hero-slide-copy span { display: -webkit-box; overflow: hidden; color: #d9f1df; font-size: 8px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
    .resident-hero-slide-copy em { align-self: flex-start; margin-top: 8px; padding: 4px 8px; color: #174c3a; background: #dff2b8; border-radius: 99px; font-size: 8px; font-style: normal; font-weight: 800; }
    .resident-hero-pager { position: absolute; z-index: 2; right: 12px; bottom: 11px; display: flex; align-items: center; gap: 4px; color: #d9f1df; font-size: 8px; }
    .resident-hero-pager button { width: 21px; height: 21px; padding: 0; color: #174c3a; background: #e7f4c9; border: 0; border-radius: 50%; font-size: 12px; line-height: 1; }
    .resident-hero-state { padding: 16px; }
    .resident-hero-state small, .resident-hero-state strong, .resident-hero-state span { position: relative; display: block; }
    .resident-points-card { padding: 14px; color: #eaf7ec; background: linear-gradient(145deg,#28674b,#174c3a); border-radius: 13px; }
    .resident-points-card small { color: #bcd9c7; font-size: 8px; }
    .resident-points-card strong { display: block; margin: 5px 0 3px; color: #fff; font-size: 32px; line-height: 1; }
    .resident-points-card span { color: #c7e0ce; font-size: 8px; }
    .resident-reward { margin-top: 8px; padding: 10px; background: #f5faf4; border: 1px solid #dcebdc; border-radius: 10px; }
    .resident-reward-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .resident-reward b { color: #385f4c; font-size: 10px; }
    .resident-reward small { display: block; margin-top: 4px; color: #789486; font-size: 8px; line-height: 1.45; }
    .resident-reward button { padding: 5px 8px; color: #174c3a; background: #dff2b8; border: 0; border-radius: 99px; font-size: 8px; font-weight: 800; }
    .resident-reward button:disabled { color: #91a49a; background: #e7eee8; cursor: not-allowed; }
    .resident-points-ledger { margin-top: 14px; }
    .resident-points-ledger h4 { margin: 0 0 6px; color: #385f4c; font-size: 10px; }
    .resident-points-ledger p { display: flex; justify-content: space-between; gap: 8px; margin: 5px 0; color: #789486; font-size: 8px; }
    .resident-points-ledger b { color: #347657; }
    .rating-stars { display: flex; gap: 5px; margin-bottom: 5px; }
    .rating-stars button { padding: 0; color: #c8d8cb; background: transparent; border: 0; font-size: 27px; line-height: 1; }
    .rating-stars button.active { color: #f2bb57; }
    .review-prompt { margin-top: 10px; padding: 11px; border: 1px solid #d7eac2; border-radius: 11px; background: #f7fbe9; }
    .review-prompt b { display: block; color: #385f4c; font-size: 10px; }
    .review-prompt span { display: block; margin: 5px 0 8px; color: #789486; font-size: 8px; }
    .worker-controls { display: grid; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid #d9e7d4; }
    .worker-controls label { color: #52705f; font-size: 10px; font-weight: 800; }
    .worker-controls textarea, .worker-controls input, .worker-controls select { width: 100%; padding: 8px; border: 1px solid #c9dbc5; border-radius: 8px; color: #234b39; background: #fff; font: inherit; font-size: 10px; }
    .worker-controls textarea { min-height: 48px; resize: vertical; }
    .worker-check-state { color: #347657; font-size: 10px; font-weight: 800; }
    .resident-content-state { display: grid; gap: 5px; padding: 18px 12px; color: #789486; text-align: center; background: #f5faf4; border: 1px solid #dcebdc; border-radius: 12px; font-size: 9px; }
    .resident-content-state b { color: #385f4c; font-size: 10px; }
    .resident-content-state button { justify-self: center; padding: 5px 10px; color: #174c3a; background: #dff2b8; border: 0; border-radius: 99px; font: inherit; font-size: 8px; font-weight: 800; cursor: pointer; }
    .resident-channel-wall { padding: 1px; }
    .resident-channel-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
    .resident-channel-head small { display: block; color: #789486; font-size: 8px; font-weight: 800; }
    .resident-channel-head b { display: block; margin-top: 2px; color: #385f4c; font-size: 13px; }
    .resident-channel-head span { color: #789486; font-size: 8px; }
    .resident-channel-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .resident-channel-card { position: relative; display: flex; min-height: 154px; overflow: hidden; padding: 0; color: #fff; text-align: left; background: linear-gradient(145deg, #28674b, #174c3a); border: 0; border-radius: 12px; box-shadow: 0 7px 17px rgba(21, 67, 45, .13); cursor: pointer; }
    .resident-channel-card:nth-child(4n + 1), .resident-channel-card:nth-child(4n + 4) { min-height: 184px; }
    .resident-channel-card.has-image { background-position: center; background-size: cover; }
    .resident-channel-card::after { position: absolute; inset: 0; content: ""; background: linear-gradient(180deg, rgba(11, 40, 29, .03) 20%, rgba(11, 40, 29, .82) 100%); }
    .resident-channel-card-copy { position: relative; z-index: 1; display: flex; flex: 1; flex-direction: column; align-self: flex-end; min-width: 0; padding: 11px; }
    .resident-channel-card-copy small { color: #d9f0da; font-size: 8px; font-weight: 800; }
    .resident-channel-card-copy b { display: block; margin-top: 4px; color: #fff; font-size: 11px; line-height: 1.35; }
    .resident-channel-card-copy span { display: block; display: -webkit-box; margin-top: 4px; overflow: hidden; color: #e4f1e4; font-size: 8px; line-height: 1.4; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
    .resident-channel-card-copy em { align-self: flex-start; margin-top: 7px; padding: 3px 7px; color: #174c3a; background: #dff2b8; border-radius: 99px; font-size: 8px; font-style: normal; font-weight: 800; }
    .resident-channel-controls { display: flex; align-items: center; justify-content: flex-end; gap: 5px; margin-top: 7px; }
    .resident-channel-controls button { width: 23px; height: 23px; padding: 0; color: #385f4c; background: #e7f1e1; border: 0; border-radius: 50%; font: inherit; font-size: 12px; cursor: pointer; }
    .resident-channel-controls span { margin-right: 2px; color: #789486; font-size: 8px; }
    .resident-content-feed h4 { margin: 0 0 7px; color: #385f4c; font-size: 10px; }
    .resident-content-item { display: block; width: 100%; margin-top: 6px; padding: 10px; color: #385f4c; text-align: left; background: #eff8f0; border: 1px solid #dcebdc; border-radius: 10px; cursor: pointer; }
    .resident-content-item b { display: block; font-size: 10px; }
    .resident-content-item span { display: block; margin-top: 3px; color: #789486; font-size: 8px; line-height: 1.5; }
    .resident-content-detail { padding: 2px 1px 10px; color: #385f4c; }
    .resident-content-detail .back-button { margin-bottom: 12px; }
    .resident-content-detail > small { color: #789486; font-size: 8px; font-weight: 800; }
    .resident-content-detail h3 { margin: 7px 0 4px; color: #234b39; font-size: 18px; line-height: 1.35; }
    .resident-article-meta { margin: 0 0 13px; color: #789486; font-size: 8px; }
    .resident-article-body { color: #52705f; font-size: 11px; line-height: 1.8; white-space: normal; }
    .live-file-input { width: 100%; padding: 8px; color: #52705f; border: 1px dashed #afd0b5; border-radius: 10px; background: #f6fbf6; font-size: 9px; }
    .upload-helper { display: block; margin-top: 5px; color: #8a9d91; font-size: 8px; }
    .live-feedback-row { padding: 9px 0; border-top: 1px solid #e6eee2; }
    .live-feedback-row:first-of-type { border-top: 0; }
    .live-feedback-row small { display: block; margin-top: 3px; color: #718a7e; }
    .resident-feedback-history { margin-top: 22px; }
    .resident-feedback-history h4 { margin: 0 0 7px; color: #385f4c; font-size: 10px; }
    .live-feedback-row textarea { width: 100%; min-height: 48px; margin-top: 7px; padding: 7px; border: 1px solid #c9dbc5; border-radius: 7px; color: #234b39; font: inherit; font-size: 10px; resize: vertical; }
    .live-feedback-actions { display: flex; align-items: center; gap: 7px; margin-top: 6px; }
    .live-feedback-actions select { flex: 1; margin: 0; padding: 7px; font-size: 10px; }
    .live-modal-backdrop { position: fixed; z-index: 90; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(21, 54, 43, .38); }
    .live-modal { width: min(440px, 100%); padding: 18px; border-radius: 15px; background: #fff; box-shadow: 0 22px 60px rgba(18, 59, 42, .25); }
    .live-modal h3 { margin: 0 0 7px; color: #234b39; font-size: 16px; }
    .live-modal p { margin: 0 0 12px; color: #718a7e; font-size: 10px; line-height: 1.5; }
    .live-modal label { display: block; margin: 10px 0 5px; color: #52705f; font-size: 10px; font-weight: 800; }
    .live-modal input, .live-modal textarea, .live-modal select { width: 100%; box-sizing: border-box; padding: 9px; border: 1px solid #c9dbc5; border-radius: 8px; color: #234b39; background: #fff; font: inherit; font-size: 11px; }
    .live-modal textarea { min-height: 72px; resize: vertical; }
    .live-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 15px; }
    .live-modal-actions button { padding: 8px 12px; border: 0; border-radius: 8px; background: #174f3c; color: #fff; font: inherit; font-size: 11px; cursor: pointer; }
    .live-modal-actions button.subtle { color: #234b39; background: #e7f1e1; }
    [data-role-disabled="true"] { cursor: not-allowed; opacity: .58; }
  `;
  document.head.appendChild(liveStyle);

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    }[character]));
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-Demo-User", demoUser);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `请求失败 (${response.status})`);
    return payload;
  }

  async function previewAttachment(url, fileName) {
    const attachmentUrl = safePublicUrl(url);
    if (!attachmentUrl) {
      notify("附件地址无效。");
      return;
    }
    const preview = window.open("", "_blank");
    try {
      const response = await fetch(attachmentUrl, { headers: { "X-Demo-User": demoUser } });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `附件请求失败 (${response.status})`);
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      if (preview) {
        preview.opener = null;
        preview.location.href = objectUrl;
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      } else {
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = fileName || "attachment";
        link.click();
        URL.revokeObjectURL(objectUrl);
        notify("浏览器拦截了预览窗口，附件已下载。");
      }
    } catch (error) {
      if (preview && !preview.closed) preview.close();
      notify(error.message);
    }
  }

  function bindAttachmentPreviews(container) {
    container?.querySelectorAll("[data-attachment-url]").forEach((button) => {
      button.addEventListener("click", () => previewAttachment(button.dataset.attachmentUrl, button.dataset.attachmentName));
    });
  }

  function readAttachmentFiles(inputId) {
    const input = document.getElementById(inputId);
    const files = [...(input?.files || [])];
    const limit = Number(input?.dataset.maxFiles) || 3;
    if (files.length > limit) return Promise.reject(new Error(`最多上传 ${limit} 个文件`));
    return Promise.all(files.slice(0, limit).map((file) => new Promise((resolve, reject) => {
      if (!ALLOWED_ATTACHMENT_TYPES.has(file.type)) {
        reject(new Error(`${file.name} 的格式不受支持`));
        return;
      }
      if (file.size > 2 * 1024 * 1024) {
        reject(new Error(`${file.name} 超过 2 MB`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => resolve({ fileName: file.name, mimeType: file.type, data: reader.result });
      reader.onerror = () => reject(new Error(`${file.name} 读取失败`));
      reader.readAsDataURL(file);
    })));
  }

  function notify(message) {
    if (typeof window.showToast === "function") {
      window.showToast(message);
      return;
    }
    window.alert(message);
  }

  function dateTime(value) {
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", month: "numeric", day: "numeric" }).format(new Date(value));
  }

  function addBanner(user, community) {
    document.querySelector(".live-banner")?.remove();
    const banner = document.createElement("div");
    banner.className = "live-banner";
    banner.innerHTML = `<span><i class="live-dot"></i><b>已连接业务数据库（测试数据）</b> · ${escapeHtml(user.name)} · ${escapeHtml(community?.name || "全平台")}</span><span>身份：${escapeHtml(user.role)}</span>`;
    document.querySelector(".topbar")?.insertAdjacentElement("afterend", banner);
  }

  function statusMarkup(ticket) {
    return `<span class="status ${statusClass[ticket.status] || "wait"}">${escapeHtml(ticket.statusLabel)}</span>`;
  }

  function setScreen(screenId, resident = false) {
    if (resident && typeof window.showResidentScreen === "function") {
      window.showResidentScreen(screenId);
      return;
    }
    if (!resident && typeof window.showScreen === "function") window.showScreen(screenId);
  }

  function gateRoleActions(role) {
    const allowed = {
      platform: new Set(),
      property: new Set(["export-tickets", "new-ticket", "submit-article", "create-announcement", "remind-ticket"]),
      resident: new Set(["create-repair", "confirm-resolved", "confirm-reopened", "submit-feedback", "payment-unavailable"]),
    }[role] || new Set();
    const labels = { platform: "平台端", property: "物业端", resident: "居民端" };
    document.querySelectorAll("[data-action]").forEach((button) => {
      const action = button.dataset.action;
      if (allowed.has(action) || button.dataset.liveBound === "true") return;
      button.disabled = true;
      button.dataset.roleDisabled = "true";
      button.title = "请切换到" + (labels[role] || "对应") + "使用此功能";
      button.setAttribute("aria-disabled", "true");
    });
  }

  function renderPlatform(data) {
    addBanner(data.user);
    const metricValues = document.querySelectorAll("#platform-overview .metric-card .metric-value");
    const metricCards = document.querySelectorAll("#platform-overview .metric-card");
    if (metricValues.length >= 5) {
      metricValues[0].textContent = String(data.metrics.companies ?? "-").padStart(2, "0");
      metricValues[1].textContent = String(data.metrics.communities ?? "-").padStart(2, "0");
      metricValues[2].textContent = Number(data.metrics.residents ?? 0).toLocaleString("zh-CN");
      metricValues[3].textContent = "待接入";
      metricValues[4].textContent = String(data.metrics.riskTickets ?? "-").padStart(2, "0");
      const revenueMeta = metricCards[3]?.querySelector(".metric-meta");
      if (revenueMeta) revenueMeta.textContent = "支付服务未配置，不显示模拟收入";
      const riskMeta = metricCards[4]?.querySelector(".metric-meta");
      if (riskMeta) riskMeta.textContent = `${data.metrics.riskTickets || 0} 项工单风险预警`;
    }
    const cards = document.querySelectorAll("#platform-projects .project-card");
    const container = cards[0]?.parentElement;
    if (container) {
      container.innerHTML = data.communities.map((community) => `
        <article class="project-card">
          <div class="project-card-top"><b>${escapeHtml(community.name)}</b><span class="severity ${community.riskTickets ? "r" : "g"}">风险 ${community.riskTickets}</span></div>
          <small>${escapeHtml(community.city)} · ${escapeHtml(community.district)} · ${escapeHtml(community.propertyCompany)}</small>
          <div class="project-stat"><div><strong>${community.residentCount.toLocaleString()}</strong><span>认证居民</span></div><div><strong>${community.openTickets}</strong><span>待跟进工单</span></div></div>
          <div class="project-footer"><span>素材 ${community.materialCount} 条</span><button class="live-project-button" data-live-community="${escapeHtml(community.slug)}">进入驾驶舱 →</button></div>
        </article>
      `).join("");
      container.querySelectorAll("[data-live-community]").forEach((button) => {
        button.addEventListener("click", () => {
          const slug = button.dataset.liveCommunity;
          window.location.href = `shengbian-property-demo.html?communityId=${encodeURIComponent(slug)}&as=platform-admin`;
        });
      });
    }

    const columnsScreen = document.getElementById("platform-columns");
    columnsScreen?.querySelector(".live-panel")?.remove();
    if (columnsScreen) {
      const reviewPanel = document.createElement("section");
      reviewPanel.className = "live-panel";
      const pendingAnnouncements = data.pendingAnnouncements || [];
      const sourceByArticle = new Map((data.pendingMaterials || []).map((material) => [material.articleId, material]));
      reviewPanel.innerHTML = `<h3>实时审核队列</h3><p>文章和物业公告通过审核后会立即出现在对应居民端首页轮播。来自“上传工作资料”的文章可查看原始四项资料、AI 分析和素材建议。</p>${data.pendingArticles.map((article) => { const source = sourceByArticle.get(article.id); return `<div class="live-review-row"><div><b>${escapeHtml(article.title)}</b><small>${escapeHtml(article.communityName)} · ${source ? `工作资料 #${source.id} · ${source.attachments.length} 个素材 · ${escapeHtml(source.analysis?.provider || '本地生成')}` : '物业周记'}</small>${source ? `<small>重点：${escapeHtml(source.weeklySummary.slice(0, 54))}…</small>` : ''}</div><button data-approve-id="${article.id}">通过并发布</button></div>`; }).join("")}${pendingAnnouncements.map((announcement) => `<div class="live-review-row"><div><b>${escapeHtml(announcement.title)}</b><small>${escapeHtml(announcement.communityName)} · 物业公告</small></div><button data-publish-announcement-id="${announcement.id}">通过并发布</button></div>`).join("")}${!data.pendingArticles.length && !pendingAnnouncements.length ? "<p>当前没有待审核内容。</p>" : ""}`;
      columnsScreen.appendChild(reviewPanel);
      reviewPanel.querySelectorAll("[data-approve-id]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await api(`/api/articles/${button.dataset.approveId}/approve`, { method: "POST" });
            notify("已发布。居民端刷新后可立即看到该周记。");
            loadPlatform();
          } catch (error) {
            notify(error.message);
          }
        });
      });
      reviewPanel.querySelectorAll("[data-publish-announcement-id]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await api(`/api/announcements/${button.dataset.publishAnnouncementId}/publish`, { method: "POST" });
            notify("公告已发布，居民端刷新后会出现在首页轮播。");
            loadPlatform();
          } catch (error) {
            notify(error.message);
          }
        });
      });
    }
    gateRoleActions("platform");
  }

  function ticketRows(tickets) {
    return tickets.map((ticket) => `<div class="repair-item" role="button" tabindex="0" data-live-ticket-id="${ticket.id}" data-ticket-status="${escapeHtml(ticket.status)}"><span>#${escapeHtml(ticket.publicId.split("-").at(-1))}<small>${dateTime(ticket.updatedAt)}</small></span><div><strong>${escapeHtml(ticket.description)}</strong><small>${escapeHtml(ticket.location)} · ${escapeHtml(ticket.category)}</small></div><span>${escapeHtml(ticket.assignee || "待分派")}</span>${statusMarkup(ticket)}</div>`).join("");
  }

  function isOverdue(ticket) {
    return Boolean(ticket.expectedAt && new Date(ticket.expectedAt).getTime() < Date.now() && ticket.status !== "resolved");
  }

  function matchesTicketFilter(ticket, filter) {
    return filter === "all" || (filter === "overdue" ? isOverdue(ticket) : ticket.status === filter);
  }

  function renderTicketDetail(card, ticket) {
    if (!card || !ticket) return;
    const attachmentText = ticket.attachments?.length ? `${ticket.attachments.length} 个附件已留存` : "没有附件";
    const attachmentLinks = (ticket.attachments || []).map((attachment) => {
      const url = safePublicUrl(attachment.url);
      return url ? `<button type="button" data-attachment-url="${escapeHtml(url)}" data-attachment-name="${escapeHtml(attachment.fileName)}" class="live-attachment-link">${escapeHtml(attachment.fileName)} · 打开</button>` : "";
    }).join("");
    const workEvidence = ticket.checkInAt ? `<p class="live-ticket-note"><b>维修执行记录</b><br />${escapeHtml(ticket.assignee || "维修人员")} 已于 ${dateTime(ticket.checkInAt)} 到场打卡。${ticket.checkInNote ? ` ${escapeHtml(ticket.checkInNote)}` : ""}${ticket.completionNote ? `<br />完工说明：${escapeHtml(ticket.completionNote)}` : ""}</p>` : "<p class=\"live-ticket-note\">维修人员尚未到场打卡。</p>";
    const review = ticket.review ? `<p class="live-ticket-note"><b>居民评价：${"★".repeat(ticket.review.score)}${"☆".repeat(5 - ticket.review.score)}</b>${ticket.review.body ? `<br />${escapeHtml(ticket.review.body)}` : ""}</p>` : "";
    card.innerHTML = `<h3>工单 ${escapeHtml(ticket.publicId)}</h3><p class="detail-title">${escapeHtml(ticket.description)}<br /><small>${escapeHtml(ticket.location)} · ${escapeHtml(ticket.resident)} · ${escapeHtml(ticket.contact || "未留联系方式")}</small></p>${statusMarkup(ticket)}<div class="detail-timeline">${ticket.events.map((event) => `<div><b>${escapeHtml(event.statusLabel)}</b>${dateTime(event.createdAt)} · ${escapeHtml(event.actor)}：${escapeHtml(event.note)}</div>`).join("")}</div>${workEvidence}<p class="live-ticket-note">${escapeHtml(attachmentText)}</p>${attachmentLinks ? `<div class="live-attachments">${attachmentLinks}</div>` : ""}${review}<div class="media-dots"><i></i><i></i><i></i></div><button class="primary-button" style="width:100%;margin-top:13px" type="button" data-action="remind-ticket" data-ticket-id="${ticket.id}">催办并记录</button>`;
    bindAttachmentPreviews(card);
  }

  function renderProperty(data) {
    addBanner(data.user, data.community);
    const heading = document.querySelector("#property-overview h2 span");
    if (heading) heading.textContent = data.community.name;
    const table = document.querySelector("#property-repairs .repair-table");
    const detailCard = document.querySelector("#property-repairs .detail-card");
    let activeFilter = "all";
    const renderFilteredTickets = () => {
      if (!table) return;
      const filtered = data.tickets.filter((ticket) => matchesTicketFilter(ticket, activeFilter));
      table.innerHTML = `<div class="repair-header"><span>工单编号</span><span>问题 / 位置</span><span>处理人</span><span>状态</span></div>${filtered.length ? ticketRows(filtered) : "<p class=\"live-ticket-note\">当前筛选没有工单。</p>"}`;
      table.querySelectorAll("[data-live-ticket-id]").forEach((row) => {
        const open = () => {
          const ticket = data.tickets.find((item) => item.id === Number(row.dataset.liveTicketId));
          if (!ticket) return;
          if (select) select.value = String(ticket.id);
          renderTicketDetail(detailCard, ticket);
          bindDetailReminder();
          refreshTransitionButtons();
          document.querySelectorAll("#property-repairs .repair-item").forEach((item) => item.classList.toggle("active", item === row));
        };
        row.addEventListener("click", open);
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
      });
    };
    const filterButtons = document.querySelectorAll("#property-repairs [data-ticket-filter]");
    filterButtons.forEach((button) => button.classList.toggle("active", button.dataset.ticketFilter === "all"));
    filterButtons.forEach((button) => {
      button.onclick = () => {
        activeFilter = button.dataset.ticketFilter;
        filterButtons.forEach((item) => item.classList.toggle("active", item === button));
        renderFilteredTickets();
      };
    });
    const queue = document.querySelector("#property-overview .queue");
    if (queue) queue.innerHTML = data.tickets.slice(0, 4).map((ticket) => `<div class="queue-row"><span class="queue-no">#${escapeHtml(ticket.publicId.split("-").at(-1))}</span><div><strong>${escapeHtml(ticket.description)}</strong><small>${escapeHtml(ticket.category)} · ${dateTime(ticket.updatedAt)}</small></div><span class="queue-person">${escapeHtml(ticket.assignee || "待分派")}</span>${statusMarkup(ticket)}</div>`).join("");

    const layout = document.querySelector("#property-repairs .repairs-layout");
    layout?.querySelector(".live-panel")?.remove();
    if (!layout) return;
    const journal = document.getElementById("property-journal");
    if (journal) {
      journal.querySelector(".live-material-panel")?.remove();
      const materialPanel = document.createElement("section");
      materialPanel.className = "live-panel live-material-panel";
      const latest = (data.materialSubmissions || [])[0];
      const latestMarkup = latest ? `<p><b>最近提交 #${latest.id}</b> · ${escapeHtml(latest.status)} · ${latest.attachments.length} 个素材<br />AI 分析：${escapeHtml((latest.analysis?.attachmentRecommendations || []).join('；') || '等待生成')}<br />文章草稿：${latest.articleId ? `#${latest.articleId}，已进入平台审核` : '尚未生成'}</p>` : "<p>本周还没有提交真实工作资料。</p>";
      materialPanel.innerHTML = `<h3>上传工作资料 · AI 分析后提交声边审核</h3><p>固定填写本周重点、未完成报修原因、下周计划，并上传可用于公众号的图片或视频。提交后，系统会保存原始资料、生成可审计分析和文章初稿，并自动进入现有平台审核队列。</p><label>本周重点工作完成情况总结</label><textarea id="material-weekly-summary" placeholder="例如：完成楼道照明、排水检查、绿化养护等"></textarea><label>本周报修未完成原因</label><textarea id="material-incomplete-reasons" placeholder="例如：第三方维保排期、等待配件或现场条件限制"></textarea><label>下周计划要为居民做的事情</label><textarea id="material-next-week-plan" placeholder="例如：电梯复查、居民回访、公共区域巡检"></textarea><label>上传公众号可用的工作图片或者视频</label><input id="material-files" data-max-files="6" class="live-file-input" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" multiple /><small class="upload-helper">最多 6 个文件，每个不超过 2 MB。图片与视频会被记录并归类，不会直接发布。</small><div class="live-panel-actions"><button type="button" data-submit-work-material>上传确认并生成文章初稿</button></div>${latestMarkup}`;
      journal.querySelector(".journal-layout")?.insertAdjacentElement("afterend", materialPanel);
      materialPanel.querySelector("[data-submit-work-material]")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const weeklySummary = materialPanel.querySelector("#material-weekly-summary").value.trim();
        const incompleteRepairReasons = materialPanel.querySelector("#material-incomplete-reasons").value.trim();
        const nextWeekPlan = materialPanel.querySelector("#material-next-week-plan").value.trim();
        if ([weeklySummary, incompleteRepairReasons, nextWeekPlan].some((value) => value.length < 5)) { notify("请完整填写三项文字资料，每项至少 5 个字。 "); return; }
        button.disabled = true;
        try {
          const attachments = await readAttachmentFiles("material-files");
          const result = await api("/api/work-materials", { method: "POST", body: JSON.stringify({ weeklySummary, incompleteRepairReasons, nextWeekPlan, attachments }) });
          notify(`资料 #${result.material.id} 已保存，AI 初稿已提交平台审核。`);
          loadProperty();
        } catch (error) { notify(error.message); button.disabled = false; }
      });
      journal.querySelectorAll("[data-open-work-material-form]").forEach((button) => button.onclick = () => materialPanel.scrollIntoView({ behavior: "smooth", block: "start" }));
      document.querySelectorAll('[data-screen-jump="property-journal"]').forEach((button) => {
        if (button.dataset.materialBound) return;
        button.dataset.materialBound = "true";
        button.addEventListener("click", () => window.setTimeout(() => materialPanel.scrollIntoView({ behavior: "smooth", block: "start" }), 0));
      });
    }
    const propertyControls = data.user.role === "property";
    const controls = document.createElement("section");
    controls.className = "live-panel";
    controls.innerHTML = `<h3>真实工单操作</h3><p>${propertyControls ? "物业负责分派、查看和催办。到场、现场图、完工说明与完工凭证请由独立维修人员小程序提交。" : "你正以平台身份查看该小区驾驶舱，数据只读。"}</p>${propertyControls ? `<select aria-label="选择工单">${data.tickets.map((ticket) => `<option value="${ticket.id}" data-version="${ticket.version}">${escapeHtml(ticket.publicId)} · ${escapeHtml(ticket.statusLabel)}</option>`).join("")}</select><div class="live-panel-actions"><button data-ticket-status="processing">受理并处理中</button><button data-ticket-status="awaiting_vendor">转第三方并催办</button><button data-remind-ticket class="subtle">只记录一次催办</button><button class="subtle" data-open-resident>查看居民端</button></div><div class="worker-controls"><label>维修人员（独立小程序账号）</label><select data-worker-name><option>王师傅</option><option>李师傅</option><option>第三方维保单位</option></select><div class="live-panel-actions"><button data-worker-action="assign">分派到维修人员</button><button class="subtle" data-open-worker>打开师傅端</button></div><span class="worker-check-state" data-worker-state></span></div>` : "<p class=\"live-readonly\">平台可跨小区查看数据，但不能代替物业执行工单操作。</p>"}`;
    layout.appendChild(controls);
    if (!propertyControls) return;
    const select = controls.querySelector("select");
    const bindDetailReminder = () => {
      const button = detailCard?.querySelector('[data-action="remind-ticket"]');
      if (!button || button.dataset.liveBound) return;
      button.dataset.liveBound = "true";
      button.addEventListener("click", async () => {
        const ticket = data.tickets.find((item) => item.id === Number(button.dataset.ticketId));
        if (!ticket || ticket.status === "resolved") {
          notify("已解决工单不能催办。");
          return;
        }
        try {
          await api(`/api/repairs/${ticket.id}/remind`, { method: "POST", body: JSON.stringify({ expectedVersion: ticket.version }) });
          notify(`已为 ${ticket.publicId} 写入催办记录。`);
          loadProperty();
        } catch (error) {
          notify(error.message);
        }
      });
    };
    const refreshTransitionButtons = () => {
      const ticket = data.tickets.find((item) => String(item.id) === select.value);
      const allowed = new Set(allowedTransitions[ticket?.status] || []);
      controls.querySelectorAll("[data-ticket-status]").forEach((button) => {
        button.disabled = !allowed.has(button.dataset.ticketStatus);
        button.title = button.disabled ? `当前状态“${ticket?.statusLabel || "未知"}”不能执行此操作` : "";
      });
    };
    select.addEventListener("change", refreshTransitionButtons);
    select.addEventListener("change", () => {
      renderTicketDetail(detailCard, data.tickets.find((item) => String(item.id) === select.value));
      bindDetailReminder();
    });
    refreshTransitionButtons();
    renderFilteredTickets();
    renderTicketDetail(detailCard, data.tickets.find((item) => String(item.id) === select.value));
    bindDetailReminder();
    controls.querySelectorAll("[data-ticket-status]").forEach((button) => {
      button.addEventListener("click", async () => {
        const nextStatus = button.dataset.ticketStatus;
        const notes = {
          processing: "物业已受理，工程人员正在处理中。",
          awaiting_vendor: "已联系第三方维保单位并记录催办。",
          awaiting_confirmation: "维修已完成，等待居民确认结果。",
        };
        try {
          await api(`/api/repairs/${select.value}/transition`, {
            method: "PATCH",
            body: JSON.stringify({ status: nextStatus, note: notes[nextStatus], assignee: nextStatus === "awaiting_vendor" ? "第三方维保单位" : "王师傅", expectedVersion: Number(select.selectedOptions[0].dataset.version) }),
          });
          notify("工单状态已写入数据库，并追加了服务事件。");
          loadProperty();
        } catch (error) {
          notify(error.message);
        }
      });
    });
    const workerNameInput = controls.querySelector("[data-worker-name]");
    const workerNoteInput = controls.querySelector("[data-worker-note]");
    const workerState = controls.querySelector("[data-worker-state]");
    const selectedTicket = () => data.tickets.find((ticket) => String(ticket.id) === select.value);
    const refreshWorkerControls = () => {
      const ticket = selectedTicket();
      if (!ticket) return;
      if (ticket.assignee && [...workerNameInput.options].some((option) => option.value === ticket.assignee)) workerNameInput.value = ticket.assignee;
      workerState.textContent = ticket.checkInAt ? `已于 ${dateTime(ticket.checkInAt)} 到场打卡${ticket.completedAt ? "，已提交完工凭证" : ""}` : "尚未到场打卡";
      controls.querySelectorAll("[data-worker-action]").forEach((button) => {
        const action = button.dataset.workerAction;
        button.disabled = ticket.status === "resolved" || (action === "check-in" && (!ticket.assignee || ticket.status === "awaiting_confirmation")) || (action === "complete" && (!ticket.checkInAt || ticket.status === "awaiting_confirmation"));
      });
    };
    select.addEventListener("change", refreshWorkerControls);
    refreshWorkerControls();
    controls.querySelectorAll("[data-worker-action]").forEach((button) => {
      button.addEventListener("click", async () => {
        const ticket = selectedTicket();
        if (!ticket) return;
        const expectedVersion = Number(select.selectedOptions[0].dataset.version);
        try {
          if (button.dataset.workerAction === "assign") {
            await api(`/api/repairs/${ticket.id}/assign`, { method: "POST", body: JSON.stringify({ workerName: workerNameInput.value, expectedVersion }) });
            notify(`已将 ${ticket.publicId} 分派给 ${workerNameInput.value}。`);
          }
          loadProperty();
        } catch (error) {
          notify(error.message);
        }
      });
    });
    controls.querySelector("[data-remind-ticket]")?.addEventListener("click", async () => {
      const ticket = data.tickets.find((item) => String(item.id) === select.value);
      if (!ticket || ticket.status === "resolved") {
        notify("已解决工单不能催办。");
        return;
      }
      try {
        await api(`/api/repairs/${ticket.id}/remind`, { method: "POST", body: JSON.stringify({ expectedVersion: ticket.version }) });
        notify("催办已写入事件时间线。");
        loadProperty();
      } catch (error) {
        notify(error.message);
      }
    });
    document.querySelectorAll('[data-action="export-tickets"]').forEach((button) => {
      button.onclick = () => {
        const csvCell = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
        const lines = [
          ["工单编号", "居民", "类型", "位置", "描述", "处理人", "状态", "更新时间"],
          ...data.tickets.map((ticket) => [ticket.publicId, ticket.resident, ticket.category, ticket.location, ticket.description, ticket.assignee || "", ticket.statusLabel, ticket.updatedAt]),
        ].map((row) => row.map(csvCell).join(","));
        const blob = new Blob(["\ufeff", lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `shengbian-repairs-${new Date().toISOString().slice(0, 10)}.csv`;
        link.click();
        URL.revokeObjectURL(url);
        notify("当前真实工单已导出为 CSV。");
      };
    });
    document.querySelectorAll('[data-action="new-ticket"]').forEach((button) => {
      button.onclick = () => {
        const backdrop = document.createElement("div");
        backdrop.className = "live-modal-backdrop";
        backdrop.innerHTML = `<form class="live-modal" data-new-ticket-form><h3>物业代居民登记工单</h3><p>工单会归属到选定居民，并进入同一条物业处理队列。请不要用此入口替代居民确认身份。</p><label>居民</label><select name="residentId" required>${(data.residents || []).map((resident) => `<option value="${resident.id}">${escapeHtml(resident.name)} · ${escapeHtml(resident.unit || "未绑定房屋")}</option>`).join("")}</select><label>问题类型</label><input name="category" required placeholder="例如：照明、电梯、门禁" /><label>发生位置</label><input name="location" required placeholder="楼栋、单元或公共区域" /><label>问题描述</label><textarea name="description" required placeholder="记录居民反映的具体问题"></textarea><label>联系电话（可选）</label><input name="contact" placeholder="默认使用居民登记电话" /><div class="live-modal-actions"><button type="button" class="subtle" data-close-modal>取消</button><button type="submit">创建工单</button></div></form>`;
        document.body.appendChild(backdrop);
        backdrop.querySelector("[data-close-modal]").addEventListener("click", () => backdrop.remove());
        backdrop.addEventListener("click", (event) => { if (event.target === backdrop) backdrop.remove(); });
        backdrop.querySelector("[data-new-ticket-form]").addEventListener("submit", async (event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const submit = form.querySelector('button[type="submit"]');
          submit.disabled = true;
          const formData = new FormData(form);
          try {
            const result = await api("/api/repairs/recorded", { method: "POST", body: JSON.stringify(Object.fromEntries(formData.entries())) });
            backdrop.remove();
            notify(`已创建工单 ${result.ticket.publicId}，居民端可以查看进度。`);
            loadProperty();
          } catch (error) {
            notify(error.message);
            submit.disabled = false;
          }
        });
      };
    });
    controls.querySelector("[data-open-resident]")?.addEventListener("click", () => {
      const ticket = selectedTicket();
      const residentKey = ticket?.residentKey || "resident-li";
      window.location.href = `shengbian-resident-demo.html?ticketId=${encodeURIComponent(select.value)}&as=${encodeURIComponent(residentKey)}`;
    });
    controls.querySelector("[data-open-worker]")?.addEventListener("click", () => {
      const ticket = selectedTicket();
      const workerByName = { "王师傅": "worker-wang", "李师傅": "worker-li" };
      const workerKey = workerByName[ticket?.assignee] || workerByName[workerNameInput.value];
      if (!workerKey) { notify("第三方维保单位暂未配置独立小程序账号，请选择王师傅或李师傅后分派。 "); return; }
      window.location.href = `shengbian-worker-demo.html?ticketId=${encodeURIComponent(ticket.id)}&as=${encodeURIComponent(workerKey)}`;
    });

    document.querySelectorAll('[data-action="submit-article"]').forEach((button) => {
      button.onclick = async () => {
        const draft = document.querySelector(".journal-draft");
        const title = draft?.querySelector("h3")?.textContent.trim() || "本周物业周记";
        const body = draft?.querySelector("p")?.textContent.trim() || "本周完成工程巡查与公共区域维护，并持续跟进居民关心的报修事项。";
        button.disabled = true;
        try {
          const existing = data.articles.find((article) => article.title === title && article.status !== "published");
          const created = existing ? { article: existing } : await api("/api/articles", { method: "POST", body: JSON.stringify({ title, body }) });
          if (created.article.status === "draft") await api(`/api/articles/${created.article.id}/submit`, { method: "POST" });
          notify("物业周记已提交平台审核。请切换到平台端审核并发布。");
          loadProperty();
        } catch (error) {
          notify(error.message);
        } finally {
          button.disabled = false;
        }
      };
    });

    document.querySelectorAll('[data-action="create-announcement"]').forEach((button) => {
      button.onclick = async () => {
        const title = document.getElementById("announcement-title")?.value.trim() || "小区物业服务公告";
        const body = document.getElementById("announcement-body")?.value.trim() || "请补充公告事实。";
        const imageUrl = document.getElementById("announcement-image-url")?.value.trim() || "";
        const linkUrl = document.getElementById("announcement-link-url")?.value.trim() || "";
        const sortOrder = Number(document.getElementById("announcement-sort-order")?.value || 0);
        try {
          const created = await api("/api/announcements", { method: "POST", body: JSON.stringify({ title, body, imageUrl, linkUrl, sortOrder }) });
          await api(`/api/announcements/${created.announcement.id}/submit`, { method: "POST" });
          notify("公告草稿已提交平台审核，不会直接对居民发布。");
          loadProperty();
        } catch (error) {
          notify(error.message);
        }
      };
    });
    const feedbackPanel = document.querySelector("#property-repairs .live-feedback-panel");
    if (feedbackPanel) feedbackPanel.remove();
    const feedbackPanelNew = document.createElement("section");
    feedbackPanelNew.className = "live-panel live-feedback-panel";
    feedbackPanelNew.innerHTML = `<h3>居民反馈</h3><p>反馈来自居民端真实提交。回复会写入消息记录并更新处理状态。</p>${(data.feedback || []).length ? data.feedback.map((item) => `<div class="live-feedback-row"><b>${escapeHtml(item.subject)}</b><small>${escapeHtml(item.body)} · ${escapeHtml(item.statusLabel || item.status)}</small>${(item.messages || []).slice(-3).map((message) => `<small>${escapeHtml(message.author)}：${escapeHtml(message.body)}</small>`).join("")}<textarea data-feedback-reply-body="${item.id}" placeholder="填写给居民的回复"></textarea><div class="live-feedback-actions"><select data-feedback-reply-status="${item.id}"><option value="processing">回复并标记处理中</option><option value="resolved">回复并标记已解决</option><option value="closed">回复并关闭</option></select><button data-feedback-reply="${item.id}">发送回复</button></div></div>`).join("") : "<p>当前没有居民反馈。</p>"}`;
    layout.appendChild(feedbackPanelNew);
    feedbackPanelNew.querySelectorAll("[data-feedback-reply]").forEach((button) => {
      button.addEventListener("click", async () => {
        const feedbackId = button.dataset.feedbackReply;
        const bodyInput = feedbackPanelNew.querySelector(`[data-feedback-reply-body="${feedbackId}"]`);
        const statusInput = feedbackPanelNew.querySelector(`[data-feedback-reply-status="${feedbackId}"]`);
        const body = bodyInput?.value.trim() || "";
        if (!body) {
          notify("请先填写回复内容。");
          return;
        }
        button.disabled = true;
        try {
          await api(`/api/feedback/${feedbackId}/reply`, { method: "POST", body: JSON.stringify({ body, status: statusInput?.value || "processing" }) });
          notify("回复已写入居民反馈记录。");
          loadProperty();
        } catch (error) {
          notify(error.message);
        } finally {
          button.disabled = false;
        }
      });
    });
    gateRoleActions("property");
  }

  function renderTicketProgress(ticket) {
    const progress = document.getElementById("resident-progress");
    if (!progress || !ticket) return;
    const helper = progress.querySelector(".form-helper");
    if (helper) helper.textContent = `服务编号 #${ticket.publicId}`;
    const intro = progress.querySelector(".progress-intro");
    if (intro) intro.innerHTML = `<small>${escapeHtml(ticket.location)}</small><b>${escapeHtml(ticket.description)}</b><span>当前：${escapeHtml(ticket.statusLabel)}</span>`;
    const timeline = progress.querySelector(".resident-timeline");
    if (timeline) timeline.innerHTML = ticket.events.map((event) => `<div class="r-step ${event.status === ticket.status ? "waiting" : ""}"><b>${escapeHtml(event.statusLabel)}</b><small>${dateTime(event.createdAt)} · ${escapeHtml(event.actor)}：${escapeHtml(event.note)}</small></div>`).join("");
    const confirmBox = progress.querySelector(".confirm-box");
    if (confirmBox) confirmBox.hidden = ticket.status !== "awaiting_confirmation";
    const reviewPrompt = progress.querySelector(".review-prompt");
    if (reviewPrompt) reviewPrompt.hidden = ticket.status !== "resolved" || Boolean(ticket.review);
    const attachments = progress.querySelector("#resident-ticket-attachments");
    if (attachments) {
      const links = (ticket.attachments || []).map((attachment) => {
        const url = safePublicUrl(attachment.url);
        return url ? `<button type="button" data-attachment-url="${escapeHtml(url)}" data-attachment-name="${escapeHtml(attachment.fileName)}" class="live-attachment-link">${escapeHtml(attachment.fileName)} · 打开</button>` : "";
      }).join("");
      attachments.innerHTML = links ? `<h4>本工单附件</h4><div class="live-attachments">${links}</div>` : "";
      bindAttachmentPreviews(attachments);
    }
  }

  function renderResidentTicketList(tickets) {
    const list = document.getElementById("resident-ticket-list");
    if (!list) return;
    list.innerHTML = `<h4>我的工单</h4>${tickets.map((ticket) => `<button class="resident-ticket-button ${ticket.id === selectedTicketId ? "active" : ""}" type="button" data-resident-ticket-id="${ticket.id}"><span><b>${escapeHtml(ticket.description)}</b><small>${escapeHtml(ticket.publicId)} · ${dateTime(ticket.updatedAt)}</small></span>${statusMarkup(ticket)}</button>`).join("")}`;
    list.querySelectorAll("[data-resident-ticket-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const ticket = tickets.find((item) => item.id === Number(button.dataset.residentTicketId));
        if (!ticket) return;
        selectedTicketId = ticket.id;
        localStorage.setItem("shengbian-last-ticket", String(ticket.id));
        renderTicketProgress(ticket);
        renderResidentReview(ticket);
        renderResidentTicketList(tickets);
      });
    });
  }

  function renderResidentHero(data) {
    const hero = document.getElementById("resident-hero-carousel");
    if (!hero) return;
    const items = (data.carousel || []).slice(0, 5);
    const fallback = [
      { title: "把社区里的每件小事，认真回应。", body: "物业、维修和社区内容都在这里持续更新。", imageUrl: "", linkUrl: "" },
      { title: "维修进度，居民全程看得见。", body: "物业受理、师傅打卡、完工凭证和居民评价，形成完整服务记录。", imageUrl: "", linkUrl: "" },
      { title: "社区好内容，随时打开。", body: "每张栏目图都可以连接物业公告、微信公众号文章或普通网址。", imageUrl: "", linkUrl: "" },
    ];
    const slides = items.length ? items : fallback;
    let index = 0;
    if (residentHeroTimer) window.clearInterval(residentHeroTimer);
    const draw = () => {
      const item = slides[index];
      const imageUrl = safePublicUrl(item.imageUrl);
      const eyebrow = index === 0 ? `你好，${data.user.name}` : (item.contentType === "article" ? "声边栏目" : "社区精选");
      hero.innerHTML = `<button class="resident-hero-slide ${imageUrl ? "has-image" : ""}" type="button" data-hero-open><span class="resident-hero-slide-copy"><small>${escapeHtml(eyebrow)}</small><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml((item.body || "").slice(0, 82))}${(item.body || "").length > 82 ? "…" : ""}</span><em>${item.linkUrl ? "点击进入 ›" : "查看详情 ›"}</em></span><span class="resident-hero-pager"><button type="button" data-hero-prev aria-label="上一张">‹</button><span>${index + 1} / ${slides.length}</span><button type="button" data-hero-next aria-label="下一张">›</button></span></button>`;
      const slide = hero.querySelector(".resident-hero-slide");
      if (imageUrl) slide.style.backgroundImage = `url("${imageUrl.replace(/"/g, "%22")}")`;
      hero.querySelector("[data-hero-open]")?.addEventListener("click", (event) => {
        if (event.target.closest("[data-hero-prev], [data-hero-next]")) return;
        const linkUrl = safePublicUrl(item.linkUrl);
        if (item.contentType === "article") {
          selectedArticleId = item.id;
          setScreen("resident-content", true);
          renderResidentContentWall(data);
        }
        else if (linkUrl) window.open(linkUrl, "_blank", "noopener");
        else notify(`${item.title}：${item.body || "社区服务持续更新中。"}`);
      });
      hero.querySelector("[data-hero-prev]")?.addEventListener("click", (event) => { event.stopPropagation(); index = (index - 1 + slides.length) % slides.length; draw(); });
      hero.querySelector("[data-hero-next]")?.addEventListener("click", (event) => { event.stopPropagation(); index = (index + 1) % slides.length; draw(); });
    };
    draw();
    if (slides.length > 1) residentHeroTimer = window.setInterval(() => { index = (index + 1) % slides.length; draw(); }, 5000);
  }

  function renderResidentPoints(data) {
    const container = document.getElementById("resident-points-content");
    if (!container) return;
    const points = data.points || { balance: 0, lifetimeEarned: 0, ledger: [], rewards: [] };
    const rewards = points.rewards.length ? points.rewards.map((reward) => `<article class="resident-reward"><div class="resident-reward-head"><b>${escapeHtml(reward.name)}</b><button type="button" data-redeem-reward="${reward.id}" ${reward.canRedeem ? "" : "disabled"}>${reward.stock > 0 ? `${reward.pointsCost} 积分兑换` : "已兑完"}</button></div><small>${escapeHtml(reward.description)} · 剩余 ${reward.stock} 份</small></article>`).join("") : "<p class=\"form-helper\">物业还没有配置可兑换礼品。</p>";
    const ledger = points.ledger.length ? points.ledger.slice(0, 5).map((item) => `<p><span>${escapeHtml(item.note)}</span><b>${item.amount > 0 ? "+" : ""}${item.amount}</b></p>`).join("") : "<p>暂无积分记录</p>";
    container.innerHTML = `<section class="resident-points-card"><small>当前可用积分</small><strong>${Number(points.balance || 0).toLocaleString("zh-CN")}</strong><span>累计获得 ${Number(points.lifetimeEarned || 0).toLocaleString("zh-CN")} 分</span></section><div class="resident-points-ledger"><h4>可兑换礼品</h4>${rewards}<h4 style="margin-top:14px">积分记录</h4>${ledger}</div>`;
    container.querySelectorAll("[data-redeem-reward]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await api("/api/points/redeem", { method: "POST", body: JSON.stringify({ rewardId: Number(button.dataset.redeemReward) }) });
          notify(`兑换申请已提交：${result.redemption.rewardName}。物业会联系你领取。`);
          loadResident();
        } catch (error) {
          notify(error.message);
          button.disabled = false;
        }
      });
    });
  }

  function renderResidentReview(ticket) {
    const reviewScreen = document.getElementById("resident-review");
    if (!reviewScreen) return;
    const helper = document.getElementById("resident-review-ticket");
    const submit = document.getElementById("submit-review");
    let score = 5;
    const scoreButtons = [...reviewScreen.querySelectorAll("[data-score]")];
    const paint = () => scoreButtons.forEach((button) => button.classList.toggle("active", Number(button.dataset.score) <= score));
    scoreButtons.forEach((button) => { button.onclick = () => { score = Number(button.dataset.score); paint(); }; });
    paint();
    if (!ticket) {
      helper.textContent = "当前没有可评价的已完成工单";
      submit.disabled = true;
      return;
    }
    helper.textContent = `${ticket.publicId} · ${ticket.description}`;
    submit.disabled = ticket.status !== "resolved" || Boolean(ticket.review);
    if (ticket.review) helper.textContent += " · 已评价";
    submit.onclick = async () => {
      if (ticket.status !== "resolved" || ticket.review) {
        notify("请在居民确认工单已解决后评价，每张工单只能评价一次。");
        return;
      }
      submit.disabled = true;
      try {
        const attachments = await readAttachmentFiles("review-attachments");
        await api(`/api/repairs/${ticket.id}/review`, { method: "POST", body: JSON.stringify({ score, body: document.getElementById("resident-review-body")?.value.trim() || "", attachments }) });
        notify("评价已提交，20 积分已到账。");
        setScreen("resident-points", true);
        loadResident();
      } catch (error) {
        notify(error.message);
        submit.disabled = false;
      }
    };
  }

  function renderResident(data) {
    addBanner(data.user, data.community);
    renderResidentHero(data);
    const preferredTicket = data.tickets.find((ticket) => ticket.id === selectedTicketId) || data.tickets[0];
    if (preferredTicket) selectedTicketId = preferredTicket.id;
    const serviceCard = document.querySelector(".mini-service-card");
    if (serviceCard && preferredTicket) {
      serviceCard.innerHTML = `<i class="service-progress-icon">◌</i><div><strong>${escapeHtml(preferredTicket.description)}</strong><small>${escapeHtml(preferredTicket.assignee || "等待物业受理")} · ${dateTime(preferredTicket.updatedAt)}</small></div>${statusMarkup(preferredTicket)}`;
    }
    renderTicketProgress(preferredTicket);
    renderResidentTicketList(data.tickets);
    renderResidentPoints(data);
    renderResidentReview(preferredTicket);
    renderResidentContentWall(data);
    const deepLinkedArticle = selectedArticleId && data.articles?.find((article) => article.id === selectedArticleId);
    if (deepLinkedArticle) setScreen("resident-content", true);
    const feedbackHistory = document.getElementById("resident-feedback-history");
    if (feedbackHistory) {
      feedbackHistory.innerHTML = (data.feedback || []).length ? `<h4>我的反馈记录</h4>${data.feedback.map((item) => `<article class="live-feedback-row"><b>${escapeHtml(item.subject)}</b><small>${escapeHtml(item.statusLabel || item.status)} · ${dateTime(item.updatedAt)}</small>${(item.messages || []).slice(-2).map((message) => `<small>${escapeHtml(message.author)}：${escapeHtml(message.body)}</small>`).join("")}</article>`).join("")}` : "";
    }

    const inputs = document.querySelectorAll("#resident-repair .fake-input");
    inputs.forEach((input) => input.removeAttribute("readonly"));
    const description = document.querySelector("#resident-repair .fake-textarea");
    description?.removeAttribute("readonly");
    const submitButton = document.getElementById("submit-repair");
    if (submitButton) {
      submitButton.onclick = async () => {
        const category = document.querySelector(".choice-row button.active")?.textContent.trim() || "其他";
        const location = inputs[0]?.value.trim() || "未填写位置";
        const text = description?.value.trim() || "未填写问题描述";
        const contact = inputs[1]?.value.trim() || "";
        submitButton.disabled = true;
        try {
        const attachments = await readAttachmentFiles("repair-attachments");
        const result = await api("/api/repairs", { method: "POST", body: JSON.stringify({ category, location, description: text, contact, attachments }) });
          selectedTicketId = result.ticket.id;
          localStorage.setItem("shengbian-last-ticket", String(result.ticket.id));
          renderTicketProgress(result.ticket);
          setScreen("resident-progress", true);
          notify(`已创建真实工单 #${result.ticket.publicId}，物业端现在可以受理。`);
        } catch (error) {
          notify(error.message);
        } finally {
          submitButton.disabled = false;
        }
      };
    }
    const confirmButtons = document.querySelectorAll("#resident-progress .confirm-actions button");
    confirmButtons.forEach((button) => {
      button.onclick = async () => {
        const ticket = data.tickets.find((item) => item.id === selectedTicketId);
        if (!ticket || ticket.status !== "awaiting_confirmation") {
          notify("该工单尚未进入“待居民确认”状态，请等待物业完成处理。");
          return;
        }
        button.disabled = true;
        try {
          const result = await api(`/api/repairs/${ticket.id}/resident-confirmation`, {
            method: "POST",
            body: JSON.stringify({ resolved: button.dataset.action === "confirm-resolved", expectedVersion: ticket.version }),
          });
          localStorage.setItem("shengbian-last-ticket", String(result.ticket.id));
          notify("确认结果已写入服务记录。");
          loadResident();
        } catch (error) {
          notify(error.message);
        } finally {
          button.disabled = false;
        }
      };
    });
    const feedbackButton = document.querySelector('[data-action="submit-feedback"]');
    if (feedbackButton) {
      feedbackButton.onclick = async () => {
        const type = document.querySelector(".feedback-type button.active")?.dataset.feedbackType || "suggestion";
        const subject = document.getElementById("feedback-subject")?.value.trim() || "";
        const body = document.getElementById("feedback-body")?.value.trim() || "";
        feedbackButton.disabled = true;
        try {
          await api("/api/feedback", { method: "POST", body: JSON.stringify({ type, subject, body }) });
          notify("反馈已提交到物业客服队列。");
          document.getElementById("feedback-subject").value = "";
          document.getElementById("feedback-body").value = "";
          setScreen("resident-home", true);
        } catch (error) {
          notify(error.message);
        } finally {
          feedbackButton.disabled = false;
        }
      };
    }
    document.querySelectorAll('[data-action="payment-unavailable"]').forEach((button) => {
      if (button.dataset.liveBound) return;
      button.dataset.liveBound = "true";
      button.addEventListener("click", () => notify("支付通道尚未配置，当前不会生成或扣款。接入微信支付或支付宝商户配置后再开放此入口。"));
    });
    document.querySelectorAll("[data-contact-phone]").forEach((button) => {
      if (button.dataset.liveBound) return;
      button.dataset.liveBound = "true";
      button.addEventListener("click", () => { window.location.href = `tel:${button.dataset.contactPhone.replace(/[^0-9+]/g, "")}`; });
    });
    gateRoleActions("resident");
  }

  async function loadPlatform() {
    renderPlatform(await api("/api/dashboard/platform"));
  }

  async function loadProperty() {
    const suffix = selectedCommunity ? `?communityId=${encodeURIComponent(selectedCommunity)}` : "";
    renderProperty(await api(`/api/dashboard/property${suffix}`));
  }

  async function loadResident() {
    renderResidentContentWallState("正在加载社区精选…");
    try {
      renderResident(await api("/api/dashboard/resident"));
      residentLoadRetryScheduled = false;
    } catch (error) {
      renderResidentContentWallState("社区精选暂时未加载成功。", "重新加载");
      document.querySelectorAll("[data-resident-content-retry]").forEach((button) => {
        button.addEventListener("click", () => loadResident(), { once: true });
      });
      if (!residentLoadRetryScheduled) {
        residentLoadRetryScheduled = true;
        window.setTimeout(() => loadResident(), 1500);
      }
      throw error;
    }
  }

  function renderWorker(data) {
    const subtitle = document.getElementById("worker-subtitle");
    if (subtitle) subtitle.textContent = `${data.worker.name} · ${data.worker.communityName} · ${data.worker.specialty || '综合维修'}`;
    const summary = document.getElementById("worker-summary");
    const active = data.tickets.filter((ticket) => !["resolved", "awaiting_confirmation"].includes(ticket.status));
    const awaiting = data.tickets.filter((ticket) => ticket.status === "awaiting_confirmation");
    if (summary) summary.innerHTML = `<div class="chip">待处理<b>${active.length}</b></div><div class="chip">待居民确认<b>${awaiting.length}</b></div><div class="chip">全部工单<b>${data.tickets.length}</b></div>`;
    const container = document.getElementById("worker-tickets");
    if (!container) return;
    const deepTicket = data.tickets.find((ticket) => ticket.id === selectedTicketId);
    const tickets = deepTicket ? [deepTicket, ...data.tickets.filter((ticket) => ticket.id !== deepTicket.id)] : data.tickets;
    if (!tickets.length) { container.innerHTML = '<div class="empty">当前没有分配给你的工单。物业分派后会自动出现在这里。</div>'; return; }
    const stage = (ticket, statuses) => statuses.some((status) => ticket.events.some((event) => event.note.includes(status)) || ticket.attachments.some((attachment) => attachment.stage === status));
    const html = tickets.map((ticket) => {
      const accepted = Boolean(ticket.expectedAt);
      const arrived = Boolean(ticket.checkInAt);
      const problemUploaded = ticket.attachments.some((attachment) => attachment.stage === "problem");
      const completed = Boolean(ticket.completedAt);
      const steps = [["接单", accepted], ["到场", arrived], ["现场图", problemUploaded], ["完工", completed]].map(([label, done]) => `<span class="step ${done ? 'done' : ''}">${label}</span>`).join("");
      const statusClassName = ticket.status === "awaiting_confirmation" || ticket.status === "resolved" ? "complete" : (ticket.status === "new" ? "waiting" : "");
      const evidence = ticket.attachments.length ? `<p><small>已留存：${ticket.attachments.map((attachment) => `${attachment.stage === 'problem' ? '现场问题' : attachment.stage === 'completion' ? '完工凭证' : '报修附件'}·${attachment.fileName}`).join('；')}</small></p>` : '';
      return `<article class="ticket" data-worker-ticket="${ticket.id}"><div class="ticket-head"><div><h2>${escapeHtml(ticket.publicId)} · ${escapeHtml(ticket.category)}</h2><p>${escapeHtml(ticket.location)}<br />${escapeHtml(ticket.description)}</p></div><span class="status ${statusClassName}">${escapeHtml(ticket.statusLabel)}</span></div><div class="steps">${steps}</div>${ticket.expectedAt ? `<p><small>预计上门：${escapeHtml(ticket.expectedAt)}</small></p>` : ''}${evidence}<details ${ticket.id === selectedTicketId ? 'open' : ''}><summary>处理这张工单</summary><label>预计上门时间</label><input data-worker-expected="${ticket.id}" type="datetime-local" value="${escapeHtml((ticket.expectedAt || '').slice(0, 16))}" ${accepted ? 'readonly' : ''} /><div class="actions"><button type="button" data-worker-accept="${ticket.id}" ${accepted || ["resolved", "awaiting_confirmation"].includes(ticket.status) ? 'disabled' : ''}>接单并确认上门时间</button></div><label>到场说明 / 处理说明</label><textarea data-worker-note="${ticket.id}" placeholder="例如：已到达现场，检查灯具线路…">${escapeHtml(ticket.checkInNote || '')}</textarea><div class="actions"><button type="button" class="subtle" data-worker-arrive="${ticket.id}" ${!accepted || arrived || ["resolved", "awaiting_confirmation"].includes(ticket.status) ? 'disabled' : ''}>到场打卡</button></div><label>现场问题图片或视频</label><input id="worker-problem-files-${ticket.id}" class="live-file-input" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" multiple /><span class="hint">最多 3 个文件，每个不超过 2 MB。</span><div class="actions"><button type="button" class="subtle" data-worker-problem="${ticket.id}" ${!accepted || ["resolved", "awaiting_confirmation"].includes(ticket.status) ? 'disabled' : ''}>上传现场问题凭证</button></div><label>完工图片或视频</label><input id="worker-complete-files-${ticket.id}" class="live-file-input" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime" multiple /><span class="hint">请填写完工说明后提交，居民会在自己的小程序确认结果。</span><div class="actions"><button type="button" data-worker-complete="${ticket.id}" ${!arrived || completed || ["resolved", "awaiting_confirmation"].includes(ticket.status) ? 'disabled' : ''}>提交完工</button></div></details></article>`;
    }).join("");
    container.innerHTML = html;
    const ticketById = (id) => data.tickets.find((ticket) => ticket.id === Number(id));
    const perform = async (id, action, payload) => {
      const ticket = ticketById(id);
      if (!ticket) return;
      try {
        await api(`/api/worker/repairs/${ticket.id}/${action}`, { method: "POST", body: JSON.stringify({ ...payload, expectedVersion: ticket.version }) });
        selectedTicketId = ticket.id;
        localStorage.setItem("shengbian-last-ticket", String(ticket.id));
        notify("操作已写入与物业、居民共用的工单时间线。 ");
        loadWorker();
      } catch (error) { notify(error.message); }
    };
    container.querySelectorAll("[data-worker-accept]").forEach((button) => button.addEventListener("click", () => {
      const id = button.dataset.workerAccept; const expectedAt = container.querySelector(`[data-worker-expected="${id}"]`)?.value;
      if (!expectedAt) { notify("请先填写预计上门时间。 "); return; } perform(id, "accept", { expectedAt });
    }));
    container.querySelectorAll("[data-worker-arrive]").forEach((button) => button.addEventListener("click", () => {
      const id = button.dataset.workerArrive; const note = container.querySelector(`[data-worker-note="${id}"]`)?.value.trim() || "维修人员已到达现场，开始处理。";
      perform(id, "arrive", { note });
    }));
    container.querySelectorAll("[data-worker-problem]").forEach((button) => button.addEventListener("click", async () => {
      const id = button.dataset.workerProblem;
      try { const attachments = await readAttachmentFiles(`worker-problem-files-${id}`); if (!attachments.length) { notify("请先选择现场问题图片或视频。 "); return; } await perform(id, "problem-media", { note: "维修人员已上传现场问题图片或视频。", attachments }); } catch (error) { notify(error.message); }
    }));
    container.querySelectorAll("[data-worker-complete]").forEach((button) => button.addEventListener("click", async () => {
      const id = button.dataset.workerComplete; const note = container.querySelector(`[data-worker-note="${id}"]`)?.value.trim() || "";
      if (note.length < 5) { notify("请填写至少 5 个字的完工说明。 "); return; }
      try { const attachments = await readAttachmentFiles(`worker-complete-files-${id}`); await perform(id, "complete", { note, attachments }); } catch (error) { notify(error.message); }
    }));
  }

  async function loadWorker() { renderWorker(await api("/api/worker/dashboard")); }

  function renderResidentContentWallState(message, actionLabel = "") {
    const targets = [document.getElementById("resident-carousel"), document.getElementById("resident-content-feed-screen")].filter(Boolean);
    const action = actionLabel ? `<button type="button" data-resident-content-retry>${escapeHtml(actionLabel)}</button>` : "";
    targets.forEach((target) => {
      target.innerHTML = `<div class="resident-content-state"><b>声边栏目</b><span>${escapeHtml(message)}</span>${action}</div>`;
    });
  }

  function renderResidentContentWall(data) {
    const targets = [document.getElementById("resident-carousel"), document.getElementById("resident-content-feed-screen")].filter(Boolean);
    const items = data.carousel || [];
    const pageSize = 4;
    const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
    let page = 0;
    if (residentCarouselTimer) window.clearInterval(residentCarouselTimer);

    const draw = () => {
      const visibleItems = items.slice(page * pageSize, (page + 1) * pageSize);
      const cards = visibleItems.map((item, index) => `<button class="resident-channel-card" type="button" data-carousel-index="${page * pageSize + index}"><span class="resident-channel-card-copy"><small>${item.contentType === "article" ? "声边栏目" : "物业公告"}</small><b>${escapeHtml(item.title)}</b><span>${escapeHtml((item.body || "").slice(0, 55))}${(item.body || "").length > 55 ? "…" : ""}</span><em>${item.linkUrl ? "点击进入 ›" : "查看详情 ›"}</em></span></button>`).join("");
      const emptyState = "<p class=\"form-helper\">暂无已发布栏目或物业公告。</p>";
      const markup = items.length ? `<div class="resident-channel-wall"><div class="resident-channel-head"><div><small>社区精选 · 多图轮播</small><b>声边栏目</b></div><span>${page + 1} / ${pageCount}</span></div><div class="resident-channel-grid">${cards}</div>${pageCount > 1 ? `<div class="resident-channel-controls"><span>自动轮播中</span><button type="button" data-carousel-prev aria-label="上一组">‹</button><button type="button" data-carousel-next aria-label="下一组">›</button></div>` : ""}</div>` : emptyState;
      targets.forEach((target) => {
        const isContentScreen = target.id === "resident-content-feed-screen";
        const selectedArticle = selectedArticleId && data.articles?.find((article) => article.id === selectedArticleId);
        const detailMarkup = selectedArticle ? `
          <article class="resident-content-detail">
            <button class="back-button" type="button" data-close-article>‹ 返回声边栏目</button>
            <small>声边栏目 · ${escapeHtml(selectedArticle.communityName || "社区")}</small>
            <h3>${escapeHtml(selectedArticle.title)}</h3>
            <p class="resident-article-meta">${selectedArticle.publishedAt ? dateTime(selectedArticle.publishedAt) : "已发布"} · ${escapeHtml(selectedArticle.author || "物业")}</p>
            <div class="resident-article-body">${escapeHtml(selectedArticle.body).replace(/\n/g, "<br />")}</div>
          </article>` : "";
        if (isContentScreen && detailMarkup && selectedArticle) {
          target.innerHTML = detailMarkup;
          target.querySelector("[data-close-article]")?.addEventListener("click", () => {
            selectedArticleId = null;
            renderResidentContentWall(data);
          });
          return;
        }
        target.innerHTML = markup;
        target.querySelectorAll("[data-carousel-index]").forEach((button) => {
          const item = items[Number(button.dataset.carouselIndex)];
          const imageUrl = safePublicUrl(item?.imageUrl);
          if (imageUrl) {
            button.classList.add("has-image");
            button.style.backgroundImage = `url("${imageUrl.replace(/"/g, "%22")}")`;
          }
          button.addEventListener("click", () => {
            const linkUrl = safePublicUrl(item?.linkUrl);
            if (item?.contentType === "article") {
              selectedArticleId = item.id;
              setScreen("resident-content", true);
              renderResidentContentWall(data);
            } else if (linkUrl) {
              window.location.href = linkUrl;
            } else if (item) {
              notify(`${item.title}：${item.body}`);
            }
          });
        });
        target.querySelector("[data-carousel-prev]")?.addEventListener("click", () => { page = (page - 1 + pageCount) % pageCount; draw(); });
        target.querySelector("[data-carousel-next]")?.addEventListener("click", () => { page = (page + 1) % pageCount; draw(); });
      });
    };
    draw();
    if (pageCount > 1) residentCarouselTimer = window.setInterval(() => { page = (page + 1) % pageCount; draw(); }, 5000);
  }

  function renderResidentCarousel(data) {
    const carousel = document.getElementById("resident-carousel");
    const feed = document.getElementById("resident-content-feed");
    const items = data.carousel || [];
    let index = 0;
    const draw = () => {
      if (!carousel) return;
      const item = items[index];
      if (!item) {
        carousel.innerHTML = "<b>暂无社区公告</b><p>物业发布公告后会在这里显示。</p>";
        return;
      }
      const imageUrl = safePublicUrl(item.imageUrl);
      const linkUrl = safePublicUrl(item.linkUrl);
      carousel.style.backgroundImage = imageUrl ? `linear-gradient(135deg, rgba(23, 76, 58, .86), rgba(23, 76, 58, .55)), url("${imageUrl.replace(/"/g, "%22")}")` : "";
      carousel.style.backgroundSize = imageUrl ? "cover" : "";
      carousel.style.backgroundPosition = imageUrl ? "center" : "";
      carousel.innerHTML = `<small>${item.contentType === "article" ? "声边社区内容" : "物业公告"}</small><b>${escapeHtml(item.title)}</b><p>${escapeHtml((item.body || "").slice(0, 70))}${(item.body || "").length > 70 ? "…" : ""}</p><button data-carousel-open>${linkUrl ? "打开链接" : "查看详情"}</button><div class="carousel-controls"><button data-carousel-prev aria-label="上一条">‹</button><button data-carousel-next aria-label="下一条">›</button></div>`;
      carousel.querySelector("[data-carousel-open]")?.addEventListener("click", () => {
        if (linkUrl) {
          window.location.href = linkUrl;
        } else if (item.contentType === "article") {
          setScreen("resident-content", true);
        } else {
          notify(`${item.title}：${item.body}`);
        }
      });
      carousel.querySelector("[data-carousel-prev]")?.addEventListener("click", () => { index = (index - 1 + items.length) % items.length; draw(); });
      carousel.querySelector("[data-carousel-next]")?.addEventListener("click", () => { index = (index + 1) % items.length; draw(); });
    };
    draw();
    if (feed) {
      const articles = data.articles || [];
      const contentMarkup = `<h4>声边社区内容</h4>${articles.length ? articles.map((article) => `<button class="resident-content-item" type="button" data-article-id="${article.id}"><b>${escapeHtml(article.title)}</b><span>${escapeHtml(article.body.slice(0, 80))}${article.body.length > 80 ? "…" : ""}</span></button>`).join("") : "<p class=\"form-helper\">暂无已发布内容。</p>"}`;
      [feed, document.getElementById("resident-content-feed-screen")].filter(Boolean).forEach((target) => {
        if (target.id === "resident-content-feed-screen" && selectedArticleId && data.articles?.some((article) => article.id === selectedArticleId)) return;
        target.innerHTML = contentMarkup;
        target.querySelectorAll("[data-article-id]").forEach((button) => button.addEventListener("click", () => {
          selectedArticleId = Number(button.dataset.articleId);
          setScreen("resident-content", true);
          renderResidentContentWall(data);
        }));
      });
    }
  }

  function safePublicUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(value, window.location.origin);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  (async () => {
    try {
      if (pageRole === "platform") await loadPlatform();
      if (pageRole === "property") await loadProperty();
      if (pageRole === "resident") await loadResident();
      if (pageRole === "worker") await loadWorker();
    } catch (error) {
      console.error("Live demo API unavailable", error);
      notify(`实时数据未连接：${error.message}`);
    }
  })();
})();
