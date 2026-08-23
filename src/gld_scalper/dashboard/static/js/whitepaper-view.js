export class WhitePaperView {
  constructor(documentElement, indexElement) {
    this.documentElement = documentElement;
    this.indexElement = indexElement;
  }

  render(markdown) {
    const lines = String(markdown || "").split(/\r?\n/);
    const blocks = [];
    let paragraph = [];
    let list = [];
    let inCode = false;
    let code = [];
    const flushParagraph = () => { if (paragraph.length) { blocks.push(`<p>${inline(paragraph.join(" "))}</p>`); paragraph = []; } };
    const flushList = () => { if (list.length) { blocks.push(`<ul>${list.map(item => `<li>${inline(item)}</li>`).join("")}</ul>`); list = []; } };
    for (const line of lines) {
      if (line.startsWith("```")) {
        flushParagraph(); flushList();
        if (inCode) { blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); code = []; }
        inCode = !inCode; continue;
      }
      if (inCode) { code.push(line); continue; }
      const heading = /^(#{1,3})\s+(.+)$/.exec(line);
      if (heading) { flushParagraph(); flushList(); const level=heading[1].length; const id=slug(heading[2]); blocks.push(`<h${level} id="${id}">${inline(heading[2])}</h${level}>`); continue; }
      if (/^[-*]\s+/.test(line)) { flushParagraph(); list.push(line.replace(/^[-*]\s+/, "")); continue; }
      if (!line.trim()) { flushParagraph(); flushList(); continue; }
      if (line.startsWith("> ")) { flushParagraph(); flushList(); blocks.push(`<blockquote>${inline(line.slice(2))}</blockquote>`); continue; }
      paragraph.push(line.trim());
    }
    flushParagraph(); flushList();
    this.documentElement.innerHTML = blocks.join("");
    const headings = [...this.documentElement.querySelectorAll("h2")];
    this.indexElement.innerHTML = headings.map(item => `<a href="#${item.id}">${escapeHtml(item.textContent)}</a>`).join("");
  }
}

function inline(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}
function slug(value) { return value.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,""); }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
