(function () {
  "use strict";

  function textOf(element) {
    return (element && element.textContent || "").trim();
  }

  function buildHero(root) {
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
    eyebrow.textContent = "Runtime field guide · Python agent system";
    hero.appendChild(eyebrow);
    hero.appendChild(title);
    if (intro) hero.appendChild(intro);

    const path = document.createElement("div");
    path.className = "dp-signal-path";
    ["MEMORY", "INTENT", "RAG", "ROUTE", "SYNTHESIS", "VERIFY", "TICKET"].forEach(function (label) {
      const item = document.createElement("span");
      item.textContent = label;
      path.appendChild(item);
    });
    hero.appendChild(path);

    const meta = document.createElement("div");
    meta.className = "dp-hero-meta";
    ["24 chapters", "25 interview drills", "42 regression tests", "4 typed repair audits"].forEach(function (label) {
      const item = document.createElement("span");
      item.textContent = label;
      meta.appendChild(item);
    });
    hero.appendChild(meta);
    root.prepend(hero);
    return hero;
  }

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

  function buildRail(content) {
    const rail = document.createElement("aside");
    rail.className = "dp-rail";
    rail.setAttribute("aria-label", "教程章节导航");
    rail.innerHTML = '<div class="dp-rail-head"><span>Execution map</span><strong>从请求到可验证交付</strong></div>';

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

  function makeQuestionsCollapsible(content) {
    const interview = Array.from(content.querySelectorAll(".dp-chapter")).find(function (chapter) {
      return /^22\./.test(chapter.dataset.title || "");
    });
    if (!interview) return;

    const headings = Array.from(interview.querySelectorAll(":scope > h3"));
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

  function rewriteRepositoryLinks() {
    document.querySelectorAll('a[href^="../"]').forEach(function (link) {
      const path = link.getAttribute("href").replace(/^\.\.\//, "");
      link.href = "https://github.com/Garrulus21yyx/DialogPilot/blob/main/" + path;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  }

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

    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        links.forEach(function (link) {
          link.classList.toggle("active", link.dataset.target === entry.target.id);
        });
        const active = links.find(function (link) { return link.classList.contains("active"); });
        if (active) active.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
    }, { rootMargin: "-18% 0px -68% 0px", threshold: 0 });
    headings.forEach(function (heading) { observer.observe(heading); });
  }

  function initialize() {
    const root = document.querySelector(".page-content > .wrapper");
    if (!root || !root.querySelector("#快速导航")) return;
    root.classList.add("dp-page");

    const hero = buildHero(root);
    const content = document.createElement("article");
    content.className = "dp-content";
    while (hero && hero.nextSibling) content.appendChild(hero.nextSibling);
    wrapChapters(content);
    makeQuestionsCollapsible(content);

    const railData = buildRail(content);
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
