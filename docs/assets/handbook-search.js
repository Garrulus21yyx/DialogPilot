(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const cards = Array.from(document.querySelectorAll("details.qa-card"));
    const hero = document.querySelector(".dp-hero");
    if (!cards.length || !hero) return;
    const panel = document.createElement("section");
    panel.className = "handbook-search";
    panel.setAttribute("aria-label", "面试问题搜索");
    const label = document.createElement("label");
    label.htmlFor = "handbook-query";
    label.textContent = "搜索问题与完整答案";
    const input = document.createElement("input");
    input.type = "search";
    input.id = "handbook-query";
    input.placeholder = "试试 HNSW、意图、权重、审批、Verifier";
    const status = document.createElement("output");
    status.setAttribute("aria-live", "polite");
    const expand = document.createElement("button");
    expand.type = "button";
    expand.textContent = "展开匹配答案";
    const collapse = document.createElement("button");
    collapse.type = "button";
    collapse.textContent = "收起全部";
    panel.append(label, input, status, expand, collapse);
    hero.append(panel);
    const entries = cards.map(card => ({card, text: card.textContent.toLocaleLowerCase()}));
    function filter() {
      const terms = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      let count = 0;
      entries.forEach(({card, text}) => {
        const match = terms.every(term => text.includes(term));
        card.hidden = !match;
        if (match) count += 1;
      });
      document.querySelectorAll(".dp-chapter").forEach(chapter => {
        const questions = Array.from(chapter.querySelectorAll(".qa-card"));
        if (questions.length) chapter.hidden = questions.every(card => card.hidden);
      });
      status.textContent = count + " / " + cards.length + " 个问题";
    }
    input.addEventListener("input", filter);
    expand.addEventListener("click", () => cards.forEach(card => {if (!card.hidden) card.open = true;}));
    collapse.addEventListener("click", () => cards.forEach(card => {card.open = false;}));
    document.querySelectorAll(".dp-rail-nav a").forEach(link => {
      link.addEventListener("click", () => { input.value = ""; filter(); });
    });
    function revealHash() {
      let id;
      try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
      const target = document.getElementById(id);
      if (target && target.matches("details.qa-card")) {
        input.value = ""; filter(); target.open = true;
        requestAnimationFrame(() => target.scrollIntoView({block: "start"}));
      }
    }
    window.addEventListener("hashchange", revealHash);
    filter();
    revealHash();
  });
}());
