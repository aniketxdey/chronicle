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
})();
