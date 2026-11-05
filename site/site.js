(() => {
  const guess = () => {
    const ua = navigator.userAgent;
    if (/Windows/.test(ua)) return "win";
    if (/Linux|X11/.test(ua) && !/Android/.test(ua)) return "linux";
    return "mac";
  };
  const setOs = (os) => {
    document.body.dataset.os = os;
    document.querySelectorAll(".os-switch button").forEach((b) => b.classList.toggle("on", b.dataset.set === os));
    try { localStorage.setItem("chronicle-os", os); } catch (_) {}
  };
  let saved = null;
  try { saved = localStorage.getItem("chronicle-os"); } catch (_) {}
  setOs(saved || guess());
  document.querySelectorAll(".os-switch button").forEach((b) => b.addEventListener("click", () => setOs(b.dataset.set)));

  document.querySelectorAll("pre").forEach((pre) => {
    const button = document.createElement("button");
    button.className = "copy";
    button.type = "button";
    button.textContent = "Copy";
    button.addEventListener("click", async () => {
      const text = pre.querySelector("code")?.innerText ?? pre.innerText;
      try {
        await navigator.clipboard.writeText(text.replace(/\n$/, ""));
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy"), 1200);
      } catch (_) {}
    });
    pre.appendChild(button);
  });

  const doc = document.getElementById("doc");
  if (!doc) return;
  const links = [...document.querySelectorAll(".side-group a")];
  const sections = links.map((link) => document.getElementById(link.hash.slice(1))).filter(Boolean);
  const toc = document.getElementById("toc");
  const headings = [...doc.querySelectorAll(":scope > h2, :scope > h3")];
  const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  headings.forEach((heading) => { if (!heading.id) heading.id = slug(heading.textContent); });

  let current = null;
  const paintToc = (section) => {
    const start = headings.indexOf(section);
    const rest = start < 0 ? [] : headings.slice(start + 1);
    const end = rest.findIndex((heading) => heading.tagName === "H2");
    const inner = end < 0 ? rest : rest.slice(0, end);
    toc.parentElement.hidden = !inner.length;
    toc.innerHTML = inner.map((heading) => `<a href="#${heading.id}">${heading.textContent}</a>`).join("");
  };
  const sync = () => {
    const y = window.scrollY + 150;
    const section = sections.filter((item) => item.offsetTop <= y).pop() || sections[0];
    if (section !== current) {
      current = section;
      links.forEach((link) => {
        const on = link.hash === `#${section.id}`;
        link.classList.toggle("on", on);
        if (on) {
          // Scroll only the sidebar: scrollIntoView would also interrupt the page's own smooth scroll.
          const nav = link.closest(".doc-nav");
          const box = link.getBoundingClientRect();
          const frame = nav.getBoundingClientRect();
          if (box.top < frame.top) nav.scrollTop -= frame.top - box.top;
          else if (box.bottom > frame.bottom) nav.scrollTop += box.bottom - frame.bottom;
        }
      });
      paintToc(section);
    }
    const inner = [...toc.querySelectorAll("a")];
    const active = inner.filter((link) => document.getElementById(link.hash.slice(1)).offsetTop <= y).pop();
    inner.forEach((link) => link.classList.toggle("on", link === active));
  };
  window.addEventListener("scroll", sync, { passive: true });
  sync();

  const text = new Map(links.map((link) => {
    const start = document.getElementById(link.hash.slice(1));
    let words = start.textContent;
    for (let node = start.nextElementSibling; node && node.tagName !== "H2"; node = node.nextElementSibling) words += ` ${node.textContent}`;
    return [link, words.toLowerCase()];
  }));
  document.getElementById("doc-filter").addEventListener("input", (event) => {
    const words = event.target.value.toLowerCase().split(/\s+/).filter(Boolean);
    links.forEach((link) => { link.hidden = !words.every((word) => text.get(link).includes(word)); link.style.display = link.hidden ? "none" : ""; });
    document.querySelectorAll(".side-group").forEach((group) => { const shown = [...group.querySelectorAll("a")].some((link) => !link.hidden); group.style.display = shown ? "" : "none"; });
  });
})();
