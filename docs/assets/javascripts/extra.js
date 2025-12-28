// OceanStream docs: minimal JS hooks.

document.addEventListener("DOMContentLoaded", () => {
  // Remove heading permalinks (¶) if present.
  document
    .querySelectorAll('a.headerlink, a[title="Permanent link"]')
    .forEach((el) => el.remove());

  document.querySelectorAll("a").forEach((a) => {
    if ((a.textContent || "").trim() === "¶") a.remove();
  });

  // Note: Copy buttons are handled by .mkdocs-shadcn-fork/shadcn/js/copy-button.js

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
