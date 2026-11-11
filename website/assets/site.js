/* LED Wall Creator website: downloads, demo video, scroll effects. */
(() => {
  "use strict";

  // Direct file links: GitHub serves the newest release's file straight away (no page in between).
  const REPO = "HenriqueSim/LED_WALL_CREATOR";
  const BASE = `https://github.com/${REPO}/releases/latest/download/`;
  const FILES = {
    windows: { file: "LEDWallCreator-windows.zip", name: "Windows", mb: 213 },
    mac: { file: "LEDWallCreator-macos.zip", name: "macOS", mb: 172 },
    linux: { file: "LEDWallCreator-linux.tar.gz", name: "Linux", mb: 326 },
  };
  let version = "";

  function detectOS() {
    const ua = navigator.userAgent || "";
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/iPhone|iPad|iPod|Android/i.test(ua)) return "mobile";
    if (/Win/i.test(p) || /Windows/i.test(ua)) return "windows";
    if (/Mac/i.test(p) || /Mac OS X/i.test(ua)) return "mac";
    if (/Linux|X11/i.test(p) || /Linux/i.test(ua)) return "linux";
    return "windows";
  }
  const os = detectOS();

  function url(key) { return BASE + FILES[key].file; }

  function applyDownloads() {
    const key = os === "mobile" ? null : os;
    document.querySelectorAll(".js-download").forEach((a) => {
      if (key) {
        a.href = url(key);
        a.setAttribute("download", "");
      }                               // phones keep the link to the Download page (the app runs on computers)
    });
    document.querySelectorAll(".js-download-label").forEach((el) => {
      el.textContent = key ? `Download for ${FILES[key].name}` : "Download for your computer";
    });
    document.querySelectorAll(".js-download-meta").forEach((el) => {
      el.textContent = key
        ? `${version ? "Version " + version + " · " : ""}${FILES[key].mb} MB · free`
        : "Available for Windows, macOS and Linux. Open this page on your computer.";
    });
    document.querySelectorAll("[data-os]").forEach((card) => {
      const k = card.dataset.os;
      card.classList.toggle("mine", k === key);
      const a = card.querySelector(".js-os-download");
      if (a) { a.href = url(k); a.setAttribute("download", ""); }
      const m = card.querySelector(".js-os-size");
      if (m) m.textContent = `${version ? "v" + version + " · " : ""}${FILES[k].mb} MB`;
    });
    if (key && document.body.dataset.page === "download") {   // your system first
      const mine = document.querySelector(`[data-os="${key}"]`);
      if (mine && mine.parentElement) mine.parentElement.prepend(mine);
    }
  }

  // Version number and real file sizes from GitHub (just data; falls back to the numbers above).
  function loadRelease() {
    const cached = sessionStorage.getItem("lwc-release");
    const use = (data) => {
      version = (data.tag_name || "").replace(/^v/, "");
      (data.assets || []).forEach((a) => {
        Object.values(FILES).forEach((f) => { if (a.name === f.file) f.mb = Math.round(a.size / 1048576); });
      });
      applyDownloads();
    };
    if (cached) { try { use(JSON.parse(cached)); return; } catch (e) { /* refetch */ } }
    fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        const slim = { tag_name: data.tag_name, assets: (data.assets || []).map((a) => ({ name: a.name, size: a.size })) };
        try { sessionStorage.setItem("lwc-release", JSON.stringify(slim)); } catch (e) { /* private mode */ }
        use(slim);
      })
      .catch(() => {});
  }

  // ---- demo video: sound button, keep every copy of the video in step with the main one --------
  const master = document.querySelector(".js-master");
  const followers = Array.from(document.querySelectorAll(".js-sync"));
  const soundBtn = document.querySelector(".js-sound");
  if (soundBtn && master) {
    soundBtn.addEventListener("click", () => {
      master.muted = !master.muted;
      if (!master.muted) master.play().catch(() => {});
      soundBtn.classList.toggle("on", !master.muted);
      soundBtn.setAttribute("aria-pressed", String(!master.muted));
      soundBtn.querySelector(".label").textContent = master.muted ? "Sound on" : "Sound off";
    });
  }
  if (master) {
    setInterval(() => {
      if (master.paused || master.readyState < 2) return;
      followers.forEach((v) => {
        if (v.paused || v.readyState < 2) return;
        if (Math.abs(v.currentTime - master.currentTime) > 0.12) v.currentTime = master.currentTime;
      });
    }, 700);
  }
  // only the visible background video plays (saves battery); it starts in step with the music
  function playInStep(v) {
    if (master && !master.paused) { try { v.currentTime = master.currentTime; } catch (e) { /* not ready */ } }
    v.play().catch(() => {});
  }

  // ---- stage background follows the feature in view -----------------------------------------
  const bgVideos = Array.from(document.querySelectorAll(".stage-bg video"));
  const label = document.querySelector(".js-stage-label");
  function showBg(key, text) {
    bgVideos.forEach((v) => {
      const on = v.dataset.bg === key;
      v.classList.toggle("show", on);
      if (on) playInStep(v); else setTimeout(() => { if (!v.classList.contains("show")) v.pause(); }, 1100);
    });
    if (label && text) label.textContent = text;
  }
  const features = document.querySelectorAll(".feature[data-bg]");
  if (features.length && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) showBg(e.target.dataset.bg, e.target.dataset.label); });
    }, { rootMargin: "-45% 0px -45% 0px" });
    features.forEach((f) => io.observe(f));
  }
  // hero video: pause the decorative copies when the hero is out of view
  const hero = document.querySelector(".hero");
  if (hero && "IntersectionObserver" in window) {
    const deco = hero.querySelectorAll(".hero-glow video, .reflection video");
    new IntersectionObserver(([e]) => {
      deco.forEach((v) => (e.isIntersecting ? playInStep(v) : v.pause()));
    }).observe(hero);
  }

  // ---- reveal on scroll, tilted screen flattens as you scroll --------------------------------
  const reveals = document.querySelectorAll(".reveal");
  if (/[?&]shot\b/.test(location.search)) {          // full-page screenshots: show everything
    document.documentElement.classList.add("shot");
    reveals.forEach((el) => el.classList.add("in"));
  } else if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.12 });
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }
  const screen = document.querySelector(".js-screen");
  if (screen) {
    const flat = () => screen.classList.toggle("flat", window.scrollY > 40);
    window.addEventListener("scroll", flat, { passive: true });
    setTimeout(() => screen.classList.add("flat"), 1600);
  }

  applyDownloads();
  loadRelease();
})();
