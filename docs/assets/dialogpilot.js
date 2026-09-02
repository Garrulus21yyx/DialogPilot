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

    const pageProfiles = {
      interview: {
        eyebrow: "代码校准 · 面试防守手册",
        signals: ["主张", "源码", "Owner", "合同", "取舍", "故障", "指标", "Trace", "边界", "追问"]
      },
      evolution: {
        eyebrow: "本地验证 · Agent 质量闭环",
        signals: ["Bad Case", "归因", "候选", "Dev", "Heldout", "E2E", "报告", "复现"]
      },
      overview: {
        eyebrow: "架构证据 · DialogPilot",
        signals: ["身份", "记忆", "意图", "RAG", "TaskGraph", "ReAct", "工具", "校验", "工单", "进化"]
      },
      tutorial: {
        eyebrow: "运行时手册 · Python Agent 系统",
        signals: ["记忆", "意图", "RAG", "任务图", "ReAct", "工具", "覆盖", "融合", "校验", "工单"]
      }
    };
    const profile = pageProfiles[mode] || pageProfiles.tutorial;
    const eyebrow = document.createElement("div");
    eyebrow.className = "dp-eyebrow";
    eyebrow.textContent = profile.eyebrow;
    hero.appendChild(eyebrow);
    hero.appendChild(title);
    if (intro) hero.appendChild(intro);

    const path = document.createElement("div");
    path.className = "dp-signal-path";
    profile.signals.forEach(function (label) {
      const item = document.createElement("span");
      item.textContent = label;
      path.appendChild(item);
    });
    hero.appendChild(path);

    const meta = document.createElement("div");
    meta.className = "dp-hero-meta";
    const chapterCount = root.querySelectorAll(":scope > h2").length;
    const questionCount = Array.from(root.querySelectorAll(":scope > h3")).filter(function (heading) {
      return /^Q\d+(?:\.\d+)?：/.test(textOf(heading));
    }).length;
    const metaByMode = {
      interview: [questionCount + " 个代码校准追问", "答案对应当前实现", "不支持的主张已标记", "STAR + 连续追问"],
      evolution: [chapterCount + " 个闭环章节", "离线受限候选", "本地 E2E", "机器报告"],
      overview: [chapterCount + " 个架构切面", "单一事实 Owner", "依赖感知 TaskGraph", "发布与学习分离"],
      tutorial: [chapterCount + " 个仓库章节", questionCount + " 个面试追问", "835 项回归测试", "本地 E2E"]
    };
    const metaLabels = metaByMode[mode] || metaByMode.tutorial;
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
        // 评测章用四段校准条对应 intent/routing/retrieval/stateful 四层合同。
        if (textOf(node).indexOf("把评测数据真正跑起来") !== -1) {
          chapter.classList.add("dp-eval-lab");
        }
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
    const heads = {
      interview: ["答题地图", "从旧答案到代码证据"],
      evolution: ["进化地图", "从线上失败到安全发布"],
      overview: ["架构地图", "从职责边界到项目讲述"],
      tutorial: ["执行地图", "从请求到可验证交付"]
    };
    const head = heads[mode] || heads.tutorial;
    rail.setAttribute("aria-label", head[0] + "章节导航");
    rail.innerHTML = '<div class="dp-rail-head"><span>' + head[0] + '</span><strong>' + head[1] + '</strong></div>';

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
      return /^Q\d+(?:\.\d+)?：/.test(textOf(heading));
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
      label.textContent = "架构图 " + String(index + 1).padStart(2, "0");
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

  // 为 Mermaid SVG 和正文图片提供统一的矢量查看器；不移动原节点，避免破坏正文布局。
  function setupDiagramViewer(content) {
    const viewer = document.createElement("div");
    viewer.className = "dp-diagram-viewer";
    viewer.hidden = true;
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-labelledby", "dp-viewer-title");
    viewer.innerHTML = [
      '<div class="dp-viewer-shell">',
      '  <header class="dp-viewer-bar">',
      '    <div><span>架构图查看器</span><strong id="dp-viewer-title">架构图</strong></div>',
      '    <div class="dp-viewer-controls" aria-label="图像缩放控制">',
      '      <button type="button" data-action="out" aria-label="缩小">−</button>',
      '      <button type="button" data-action="reset" class="dp-viewer-scale" aria-label="恢复原始缩放">100%</button>',
      '      <button type="button" data-action="in" aria-label="放大">＋</button>',
      '      <button type="button" data-action="close" class="dp-viewer-close" aria-label="关闭大图">×</button>',
      '    </div>',
      '  </header>',
      '  <div class="dp-viewer-canvas">',
      '    <img alt="" draggable="false">',
      '  </div>',
      '  <footer>滚轮或双指缩放 · 放大后拖拽查看 · Esc 关闭</footer>',
      '</div>'
    ].join("");
    document.body.appendChild(viewer);

    const canvas = viewer.querySelector(".dp-viewer-canvas");
    const image = canvas.querySelector("img");
    const title = viewer.querySelector("#dp-viewer-title");
    const scaleLabel = viewer.querySelector(".dp-viewer-scale");
    const closeButton = viewer.querySelector('[data-action="close"]');
    let scale = 1;
    let offsetX = 0;
    let offsetY = 0;
    let objectUrl = "";
    let previousFocus = null;
    let dragging = false;
    let dragStart = null;
    const pointers = new Map();
    let pinchStart = null;

    function clamp(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function updateTransform() {
      if (scale <= 1) {
        offsetX = 0;
        offsetY = 0;
      }
      image.style.transform = "translate(" + offsetX + "px," + offsetY + "px) scale(" + scale + ")";
      scaleLabel.textContent = Math.round(scale * 100) + "%";
      canvas.classList.toggle("can-pan", scale > 1);
      canvas.classList.toggle("is-panning", dragging);
    }

    function setScale(nextScale) {
      scale = clamp(nextScale, 0.75, 6);
      updateTransform();
    }

    function resetView() {
      scale = 1;
      offsetX = 0;
      offsetY = 0;
      updateTransform();
    }

    function sourceFor(target) {
      if (target.tagName.toLowerCase() === "svg") {
        const clone = target.cloneNode(true);
        clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
        const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml;charset=utf-8" });
        objectUrl = URL.createObjectURL(blob);
        return objectUrl;
      }
      return target.currentSrc || target.src;
    }

    function openViewer(target, explicitLabel) {
      previousFocus = document.activeElement;
      const panel = target.closest(".mermaid-panel");
      const caption = panel && panel.querySelector(".diagram-label");
      const label = explicitLabel || target.getAttribute("alt") || textOf(caption) || "架构图";
      title.textContent = label;
      image.alt = label + " 放大视图";
      image.src = sourceFor(target);
      resetView();
      viewer.hidden = false;
      document.body.classList.add("dp-viewer-open");
      closeButton.focus();
    }

    function closeViewer() {
      if (viewer.hidden) return;
      viewer.hidden = true;
      document.body.classList.remove("dp-viewer-open");
      image.removeAttribute("src");
      pointers.clear();
      pinchStart = null;
      dragging = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = "";
      }
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
    }

    function decorate(target) {
      if (target.dataset.dpZoomReady === "true") return;
      target.dataset.dpZoomReady = "true";
      target.classList.add("dp-zoomable-media");

      const panel = target.closest(".mermaid-panel");
      if (panel && panel.dataset.dpZoomReady !== "true") {
        const caption = panel.querySelector(".diagram-label");
        const label = textOf(caption) || "架构图";
        panel.dataset.dpZoomReady = "true";
        panel.dataset.dpDiagramTitle = label;
        panel.classList.add("is-zoomable");
        panel.tabIndex = 0;
        panel.setAttribute("role", "button");
        panel.setAttribute("aria-label", label + "，点击放大");
        panel.addEventListener("click", function () { openViewer(target, label); });
        panel.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openViewer(target, label);
          }
        });

        const hint = document.createElement("span");
        hint.className = "diagram-zoom-hint";
        hint.textContent = "点击放大 ↗";
        hint.setAttribute("aria-hidden", "true");
        panel.appendChild(hint);
        return;
      }

      // 正文图片没有图卡容器，因此由图片自身承担同一查看器的入口语义。
      if (!panel) {
        target.tabIndex = 0;
        target.setAttribute("role", "button");
        target.setAttribute("aria-label", (target.getAttribute("alt") || "图片") + "，点击放大");
        target.addEventListener("click", function () { openViewer(target); });
        target.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openViewer(target);
          }
        });
      }
    }

    function discoverMedia() {
      content.querySelectorAll(".mermaid-panel svg, img").forEach(decorate);
    }

    new MutationObserver(discoverMedia).observe(content, { childList: true, subtree: true });
    discoverMedia();

    viewer.querySelector(".dp-viewer-controls").addEventListener("click", function (event) {
      const action = event.target.closest("button") && event.target.closest("button").dataset.action;
      if (action === "in") setScale(scale + 0.5);
      if (action === "out") setScale(scale - 0.5);
      if (action === "reset") resetView();
      if (action === "close") closeViewer();
    });
    viewer.addEventListener("click", function (event) {
      if (event.target === viewer) closeViewer();
    });
    canvas.addEventListener("dblclick", function () {
      if (scale > 1) resetView();
      else setScale(2);
    });
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      setScale(scale + (event.deltaY < 0 ? 0.2 : -0.2));
    }, { passive: false });
    canvas.addEventListener("pointerdown", function (event) {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      canvas.setPointerCapture(event.pointerId);
      if (pointers.size === 1 && scale > 1) {
        dragging = true;
        dragStart = { x: event.clientX - offsetX, y: event.clientY - offsetY };
      } else if (pointers.size === 2) {
        const points = Array.from(pointers.values());
        pinchStart = {
          distance: Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y),
          scale: scale
        };
        dragging = false;
      }
      updateTransform();
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2 && pinchStart) {
        const points = Array.from(pointers.values());
        const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
        setScale(pinchStart.scale * distance / Math.max(1, pinchStart.distance));
      } else if (dragging && dragStart) {
        offsetX = event.clientX - dragStart.x;
        offsetY = event.clientY - dragStart.y;
        updateTransform();
      }
    });
    function releasePointer(event) {
      pointers.delete(event.pointerId);
      if (pointers.size < 2) pinchStart = null;
      if (!pointers.size) {
        dragging = false;
        dragStart = null;
      }
      updateTransform();
    }
    canvas.addEventListener("pointerup", releasePointer);
    canvas.addEventListener("pointercancel", releasePointer);
    document.addEventListener("keydown", function (event) {
      if (viewer.hidden) return;
      if (event.key === "Escape") closeViewer();
      if (event.key === "+" || event.key === "=") setScale(scale + 0.5);
      if (event.key === "-") setScale(scale - 0.5);
      if (event.key === "0") resetView();
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

  // 教程、面经、架构、讲述和进化页共用同一阅读系统。
  function initialize() {
    const root = document.querySelector(".page-content > .wrapper");
    if (!root) return;
    const pageTitle = textOf(root.querySelector(":scope > h1"));
    let mode = "";
    if (root.querySelector("#面经使用说明") || pageTitle.indexOf("面试追问") !== -1) mode = "interview";
    else if (pageTitle.indexOf("进化") !== -1) mode = "evolution";
    else if (pageTitle.indexOf("架构边界") !== -1 || pageTitle.indexOf("项目讲述") !== -1) mode = "overview";
    else if (root.querySelector("#快速导航")) mode = "tutorial";
    if (!mode) return;
    root.classList.add("dp-page");
    root.classList.add("dp-" + mode + "-page");

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
    setupDiagramViewer(content);
    renderMermaid();
    setupScrollState(railData.headings, railData.links);
  }

  document.addEventListener("DOMContentLoaded", initialize);
}());
