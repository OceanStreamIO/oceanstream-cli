// OceanStream docs: minimal JS hooks.

document.addEventListener("DOMContentLoaded", () => {
  // Remove heading permalinks (¶) if present.
  document
    .querySelectorAll('a.headerlink, a[title="Permanent link"]')
    .forEach((el) => el.remove());

  document.querySelectorAll("a").forEach((a) => {
    if ((a.textContent || "").trim() === "¶") a.remove();
  });

  // ========================================
  // Copy buttons for Jupyter notebook code blocks
  // (Regular code blocks handled by shadcn theme, but notebooks use .highlight-ipynb)
  // ========================================
  
  // SVG clipboard icon
  const clipboardIcon = () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", "9");
    rect.setAttribute("y", "9");
    rect.setAttribute("width", "13");
    rect.setAttribute("height", "13");
    rect.setAttribute("rx", "2");
    rect.setAttribute("ry", "2");
    
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1");
    
    svg.appendChild(rect);
    svg.appendChild(path);
    return svg;
  };
  
  // Check icon for "copied" feedback
  const checkIcon = () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("points", "20 6 9 17 4 12");
    
    svg.appendChild(polyline);
    return svg;
  };
  
  // Copy handler
  const onCodeCopy = (event) => {
    const button = event.currentTarget;
    const codeBlock = button.closest(".highlight-ipynb");
    const pre = codeBlock?.querySelector("pre");
    if (!pre) return;
    
    const text = pre.textContent || "";
    navigator.clipboard.writeText(text).then(() => {
      // Show check icon
      button.innerHTML = "";
      button.appendChild(checkIcon());
      button.classList.add("copied");
      button.setAttribute("data-copied", "true");
      
      // Reset after 2 seconds
      setTimeout(() => {
        button.innerHTML = "";
        const span = document.createElement("span");
        span.className = "os-copy-label";
        span.textContent = "Copy";
        button.appendChild(span);
        button.appendChild(clipboardIcon());
        button.classList.remove("copied");
        button.removeAttribute("data-copied");
      }, 2000);
    });
  };
  
  // Add copy buttons to notebook code blocks
  document.querySelectorAll("div.highlight-ipynb").forEach((codeBlock) => {
    // Skip if already has a copy button
    if (codeBlock.querySelector("button.os-code-copy-button")) return;
    
    const button = document.createElement("button");
    button.className = "os-code-copy-button";
    button.type = "button";
    button.title = "Copy code";
    
    // Accessible label (visually hidden)
    const span = document.createElement("span");
    span.className = "os-copy-label";
    span.textContent = "Copy";
    button.appendChild(span);
    
    button.appendChild(clipboardIcon());
    button.addEventListener("click", onCodeCopy);
    codeBlock.appendChild(button);
  });

  // Right sidebar TOC: update active section as the user scrolls.
  const toc = document.getElementById("toc-sidebar");
  if (toc) {
    const tocLinks = Array.from(toc.querySelectorAll('a[href^="#"]'));
    const headingForLink = new Map();

    for (const link of tocLinks) {
      const href = link.getAttribute("href") || "";
      const id = href.startsWith("#") ? href.slice(1) : "";
      if (!id) continue;

      const heading = document.getElementById(id);
      if (!heading) continue;
      headingForLink.set(link, heading);
    }

    const setActiveLink = (activeLink) => {
      for (const link of tocLinks) {
        const isActive = link === activeLink;
        link.setAttribute("data-active", isActive ? "true" : "false");
        if (isActive) {
          link.setAttribute("aria-current", "location");
        } else {
          link.removeAttribute("aria-current");
        }
      }
    };

    // Prefer an IntersectionObserver-based scrollspy; fall back to a scroll handler.
    const headings = Array.from(headingForLink.values());
    if (headings.length > 0 && "IntersectionObserver" in window) {
      let current = null;
      const headingToLink = new Map(
        Array.from(headingForLink.entries()).map(([link, heading]) => [heading, link]),
      );

      const observer = new IntersectionObserver(
        (entries) => {
          // Pick the closest-to-top visible heading.
          const visible = entries
            .filter((e) => e.isIntersecting)
            .sort((a, b) => (a.boundingClientRect.top ?? 0) - (b.boundingClientRect.top ?? 0));

          if (visible.length > 0) {
            const next = headingToLink.get(visible[0].target) || null;
            if (next && next !== current) {
              current = next;
              setActiveLink(current);
            }
          }
        },
        {
          // Consider a heading "active" once it enters the top ~30% of the viewport.
          root: null,
          threshold: [0, 0.1, 0.5, 1],
          rootMargin: "-10% 0px -70% 0px",
        },
      );

      headings.forEach((h) => observer.observe(h));
    } else if (headings.length > 0) {
      const headingToLink = new Map(
        Array.from(headingForLink.entries()).map(([link, heading]) => [heading, link]),
      );

      const onScroll = () => {
        const offset = 120;
        let best = null;
        for (const heading of headings) {
          const rect = heading.getBoundingClientRect();
          if (rect.top <= offset) {
            best = heading;
          } else {
            break;
          }
        }
        const link = best ? headingToLink.get(best) : null;
        if (link) setActiveLink(link);
      };

      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
    }

    // When clicking a TOC entry, update immediately.
    tocLinks.forEach((link) => {
      link.addEventListener("click", () => setActiveLink(link));
    });
  }
});
