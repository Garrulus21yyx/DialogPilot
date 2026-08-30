(function () {
  "use strict";

  // 统一读取元素文本，避免空节点让后续目录构建失败。
  function textOf(element) {
    return (element && element.textContent || "").trim();
  }

  // 把 Markdown 标题和导语提升为运行时主链 Hero，不复制正文内容。
  function buildHero(root, mode) {
    const title = root.querySelector(":scope > h1");
    if (!title) return null;

    const intro = title.nextElementSibling && title.nextElementSibling.tagName === "BLOCKQUOTE"
      ? title.nextElementSibling
      : null;
    const hero = document.createElement("section");
    hero.className = "dp-hero";
    hero.setAttribute("aria-labelledby", title.id);

    const eyebrow = document.createElement("div");
    eyebrow.className = "dp-eyebrow";
    eyebrow.textContent = mode === "interview"
      ? "Evidence-checked interview dossier · DialogPilot"
      : "Runtime field guide · Python agent system";
    hero.appendChild(eyebrow);
    hero.appendChild(title);
    if (intro) hero.appendChild(intro);

    const path = document.createElement("div");
    path.className = "dp-signal-path";
    const signalLabels = mode === "interview"
      ? ["CLAIM", "CODE", "OWNER", "CONTRACT", "TRADE-OFF", "FAILURE", "METRIC", "TRACE", "BOUNDARY", "FOLLOW-UP"]
      : ["MEMORY", "INTENT", "RAG", "TASK PLAN", "REACT", "TOOLS", "COVERAGE", "SYNTHESIS", "VERIFY", "TICKET"];
    signalLabels.forEach(function (label) {
      const item = document.createElement("span");
      item.textContent = label;
      path.appendChild(item);
    });
    hero.appendChild(path);

    const meta = document.createElement("div");
    meta.className = "dp-hero-meta";
    const metaLabels = mode === "interview"
      ? ["68 evidence-checked questions", "current-code answers", "unsupported claims flagged", "STAR + follow-up drills"]
      : ["27 numbered chapters", "64 interview drills", "81 regression tests", "8 boundary repairs"];
    metaLabels.forEach(function (label) {
      const item = document.createElement("span");
      item.textContent = label;
      meta.appendChild(item);
    });
    hero.appendChild(meta);
    root.prepend(hero);
    return hero;
  }

  // 以 h2 为章节权威边界，把平铺的 Jekyll HTML 包装成独立卡片。
  function wrapChapters(content) {
    const children = Array.from(content.children);
    let chapter = null;
    let sectionIndex = 0;

    children.forEach(function (node) {
      if (node.tagName === "H2") {
        chapter = document.createElement("section");
        chapter.className = "dp-chapter";
        chapter.dataset.title = textOf(node);
        if (node.id === "快速导航") chapter.classList.add("dp-quick-nav");
        node.dataset.section = String(sectionIndex).padStart(2, "0");
        sectionIndex += 1;
        content.insertBefore(chapter, node);
        chapter.appendChild(node);
      } else if (chapter) {
        chapter.appendChild(node);
      }
    });
  }

  // 根据实际章节动态生成左侧执行轨道，避免手工目录与正文漂移。
  function buildRail(content, mode) {
    const rail = document.createElement("aside");
    rail.className = "dp-rail";
    rail.setAttribute("aria-label", mode === "interview" ? "面经章节导航" : "教程章节导航");
    rail.innerHTML = mode === "interview"
      ? '<div class="dp-rail-head"><span>Defense map</span><strong>从旧答案到代码证据</strong></div>'
      : '<div class="dp-rail-head"><span>Execution map</span><strong>从请求到可验证交付</strong></div>';

    const nav = document.createElement("nav");
    nav.className = "dp-rail-nav";
    const headings = Array.from(content.querySelectorAll(".dp-chapter > h2:first-child"));
    headings.forEach(function (heading, index) {
      const link = document.createElement("a");
      link.href = "#" + heading.id;
      link.dataset.target = heading.id;

      const step = document.createElement("span");
      step.className = "dp-step";
      step.textContent = String(index).padStart(2, "0");
      const label = document.createElement("span");
      label.textContent = textOf(heading).replace(/^\d+\.\s*/, "");
      link.append(step, label);
      nav.appendChild(link);
    });
    rail.appendChild(nav);
    return { rail: rail, headings: headings, links: Array.from(nav.querySelectorAll("a")) };
  }

  // 将所有 Q 编号追问改造成原生 details，保留键盘操作和无脚本降级能力。
  function makeQuestionsCollapsible(content) {
    const headings = Array.from(content.querySelectorAll(".dp-chapter > h3")).filter(function (heading) {
      return /^Q\d+：/.test(textOf(heading));
    });
    headings.forEach(function (heading, index) {
      const details = document.createElement("details");
      details.className = "qa-card";
      details.id = heading.id;
      if (index === 0) details.open = true;

      const summary = document.createElement("summary");
      summary.textContent = textOf(heading);
      const answer = document.createElement("div");
      answer.className = "qa-answer";

      let cursor = heading.nextElementSibling;
      while (cursor && cursor.tagName !== "H3") {
        const next = cursor.nextElementSibling;
        answer.appendChild(cursor);
        cursor = next;
      }
      heading.replaceWith(details);
      details.append(summary, answer);
    });
  }

  // 把 Markdown 代码块交给 Mermaid 渲染为 SVG；失败只影响图，不阻塞正文。
  function renderMermaid() {
    const blocks = Array.from(document.querySelectorAll("pre > code.language-mermaid"));
    if (!blocks.length || !window.mermaid) return;

    blocks.forEach(function (code, index) {
      const panel = document.createElement("figure");
      panel.className = "mermaid-panel";
      const label = document.createElement("figcaption");
      label.className = "diagram-label";
      label.textContent = "Architecture diagram " + String(index + 1).padStart(2, "0");
      const diagram = document.createElement("div");
      diagram.className = "mermaid";
      diagram.textContent = code.textContent;
      panel.append(label, diagram);
      code.parentElement.replaceWith(panel);
    });

    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        primaryColor: "#eaf3fa",
        primaryTextColor: "#183247",
        primaryBorderColor: "#4f88b5",
        lineColor: "#58758d",
        secondaryColor: "#edf8f2",
        tertiaryColor: "#fff6e9",
        fontFamily: "Avenir Next, Noto Sans SC, sans-serif"
      },
      flowchart: { curve: "basis", htmlLabels: true },
      sequence: { mirrorActors: false, useMaxWidth: true }
    });
    window.mermaid.run({ querySelector: ".mermaid" }).catch(function (error) {
      console.error("DialogPilot diagram rendering failed", error);
    });
  }

  // Pages 与源码目录层级不同，将相对源码链接改写为私人仓库浏览链接。
  function rewriteRepositoryLinks() {
    document.querySelectorAll('a[href^="../"]').forEach(function (link) {
      const path = link.getAttribute("href").replace(/^\.\.\//, "");
      link.href = "https://github.com/Garrulus21yyx/DialogPilot/blob/main/" + path;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  }

  // 维护阅读进度、返回顶部按钮和当前章节高亮三类滚动状态。
  function setupScrollState(headings, links) {
    const progress = document.createElement("div");
    progress.className = "reading-progress";
    progress.setAttribute("aria-hidden", "true");
    document.body.appendChild(progress);

    const top = document.createElement("button");
    top.className = "back-to-top";
    top.type = "button";
    top.setAttribute("aria-label", "返回顶部");
    top.textContent = "↑";
    top.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    document.body.appendChild(top);

    function updateProgress() {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = max > 0 ? Math.min(1, window.scrollY / max) : 0;
      progress.style.width = (ratio * 100).toFixed(2) + "%";
      top.classList.toggle("visible", window.scrollY > 700);
    }
    window.addEventListener("scroll", updateProgress, { passive: true });
    updateProgress();

    // 只滚动目录自己的容器；scrollIntoView 会连带滚动 document，导致锚点跳转后正文被拉回。
    function revealActiveNavigation(active) {
      const rail = active.closest(".dp-rail");
      const nav = active.closest(".dp-rail-nav");
      if (!rail || !nav) return;

      if (window.matchMedia("(max-width: 760px)").matches) {
        const left = active.offsetLeft;
        const right = left + active.offsetWidth;
        if (left < nav.scrollLeft) nav.scrollLeft = left;
        else if (right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = right - nav.clientWidth;
        return;
      }

      const top = active.offsetTop;
      const bottom = top + active.offsetHeight;
      if (top < rail.scrollTop) rail.scrollTop = top;
      else if (bottom > rail.scrollTop + rail.clientHeight) rail.scrollTop = bottom - rail.clientHeight;
    }

    // IntersectionObserver 只观察章节标题，减少长页面滚动时的计算量。
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        links.forEach(function (link) {
          link.classList.toggle("active", link.dataset.target === entry.target.id);
        });
        const active = links.find(function (link) { return link.classList.contains("active"); });
        if (active) revealActiveNavigation(active);
      });
    }, { rootMargin: "-18% 0px -68% 0px", threshold: 0 });
    headings.forEach(function (heading) { observer.observe(heading); });
  }

  // 教程和面经页共用同一阅读系统，其他 Jekyll 页面保持 Minima 原结构。
  function initialize() {
    const root = document.querySelector(".page-content > .wrapper");
    if (!root) return;
    const mode = root.querySelector("#面经使用说明") ? "interview" : "tutorial";
    if (mode === "tutorial" && !root.querySelector("#快速导航")) return;
    root.classList.add("dp-page");
    if (mode === "interview") root.classList.add("dp-interview-page");

    const hero = buildHero(root, mode);
    const content = document.createElement("article");
    content.className = "dp-content";
    while (hero && hero.nextSibling) content.appendChild(hero.nextSibling);
    wrapChapters(content);
    makeQuestionsCollapsible(content);

    const railData = buildRail(content, mode);
    const shell = document.createElement("div");
    shell.className = "dp-shell";
    shell.append(railData.rail, content);
    root.appendChild(shell);

    rewriteRepositoryLinks();
    renderMermaid();
    setupScrollState(railData.headings, railData.links);
  }

  document.addEventListener("DOMContentLoaded", initialize);
}());
