// BLUE editor highlighting. Shapes only: the words come from GET /api/blue/grammar,
// which is built from blue.py. Used by app.js in the page and by a Node test.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BlueHighlight = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Used until the grammar arrives, so the first paint is not plain.
  const FALLBACK = {
    events: ["donation", "timer", "goal"],
    keywords: [],
    fields: [],
    operators: ["equal", "equals", "above", "below"],
    is_word: "is",
    modulo: ["modulo", "modulus", "mod", "%"],
    logic: ["on", "if", "and", "or", "not", "then"],
    actions: ["send", "chat", "notice", "to"],
    chat_targets: ["youtube", "rumble", "kick", "all", "platform"],
    notice_targets: ["app", "log", "system"],
    placeholders: [],
    placeholder_aliases: {},
    time_pattern: "\\d{1,2}:\\d{2}(?::\\d{2})?(?:\\s*[AaPp][Mm])?",
    number_pattern: "\\$?\\d+(?:\\.\\d+)?",
    placeholder_pattern: "\\{([A-Za-z][A-Za-z_]*)\\}",
    word_joiners: "-_",
    payload_quote: '"',
    value_quote: "'",
  };

  function compile(grammar) {
    const g = Object.assign({}, FALLBACK, grammar || {});
    const set = (list) => new Set((list || []).map((word) => String(word).toLowerCase()));
    return {
      events: set(g.events),
      fields: set(g.fields),
      operators: set(g.operators),
      isWord: String(g.is_word || "is").toLowerCase(),
      modulo: set(g.modulo),
      logic: set(g.logic),
      actions: set(g.actions),
      chatTargets: set(g.chat_targets),
      noticeTargets: set(g.notice_targets),
      placeholders: set(g.placeholders),
      aliases: Object.assign({}, g.placeholder_aliases || {}),
      time: new RegExp(g.time_pattern, "y"),
      number: new RegExp(g.number_pattern, "y"),
      placeholder: new RegExp(g.placeholder_pattern, "g"),
      joiners: String(g.word_joiners || "-_"),
      payloadQuote: g.payload_quote || '"',
      valueQuote: g.value_quote || "'",
    };
  }

  const LETTER = /\p{L}/u;
  const SPACE = /\s/u;

  // A quoted run, with backslash escapes, as blue.py reads it. Open at the end: closed = false.
  function quoted(line, start, mark) {
    let index = start + 1;
    while (index < line.length) {
      const char = line[index];
      if (char === "\\" && index + 1 < line.length) { index += 2; continue; }
      if (char === mark) return { end: index + 1, closed: true };
      index += 1;
    }
    return { end: line.length, closed: false };
  }

  // Raw pieces in the order blue.py's _tokenize finds them, plus the gaps between them.
  function pieces(line, c) {
    const out = [];
    let index = 0;
    const push = (shape, end) => { out.push({ shape, text: line.slice(index, end), start: index, end }); index = end; };
    while (index < line.length) {
      const char = line[index];
      if (SPACE.test(char)) {
        let end = index + 1;
        while (end < line.length && SPACE.test(line[end])) end += 1;
        push("space", end);
        continue;
      }
      if (char === c.payloadQuote || char === c.valueQuote) {
        const run = quoted(line, index, char);
        push(run.closed ? (char === c.payloadQuote ? "payload" : "value") : "open", run.end);
        continue;
      }
      c.time.lastIndex = index;
      const timed = c.time.exec(line);
      if (timed) { push("time", index + timed[0].length); continue; }
      c.number.lastIndex = index;
      const number = c.number.exec(line);
      if (number) { push("number", index + number[0].length); continue; }
      if (char === "%" || char === "-") { push("mark", index + 1); continue; }
      if (LETTER.test(char)) {
        let end = index + 1;
        while (end < line.length && (LETTER.test(line[end]) || c.joiners.includes(line[end]))) end += 1;
        push("word", end);
        continue;
      }
      push("other", index + 1);
    }
    return out;
  }

  function placeholderKey(name, c) {
    const key = name.replace(/_/g, "").toLowerCase();
    return c.aliases[key] || key;
  }

  // A double-quoted message, split so its {PLACEHOLDERS} can be colored.
  function payloadParts(token, c) {
    const parts = [];
    let last = 0;
    c.placeholder.lastIndex = 0;
    let match;
    while ((match = c.placeholder.exec(token.text)) !== null) {
      if (match.index > last) parts.push({ type: "payload", text: token.text.slice(last, match.index) });
      const known = c.placeholders.has(placeholderKey(match[1], c));
      parts.push({ type: known ? "placeholder" : "placeholder-unknown", text: match[0] });
      last = match.index + match[0].length;
    }
    if (last < token.text.length) parts.push({ type: "payload", text: token.text.slice(last) });
    return parts;
  }

  // Tokens covering the whole line: [{type, text}]. Joining the texts gives the line back.
  function tokenize(line, grammar) {
    const c = grammar && grammar.events instanceof Set ? grammar : compile(grammar);
    const raw = pieces(String(line), c);
    const solid = raw.filter((piece) => piece.shape !== "space");
    const out = [];
    let afterThen = false;
    let previous = null;
    let solidIndex = 0;
    for (const piece of raw) {
      if (piece.shape === "space") { out.push({ type: "space", text: piece.text }); continue; }
      const next = solid[solidIndex + 1] || null;
      solidIndex += 1;
      let type = piece.shape;
      if (piece.shape === "word") {
        const word = piece.text.toLowerCase();
        if (word === "then") { type = "logic"; afterThen = true; }
        else if (previous && previous.text.toLowerCase() === "on" && c.events.has(word)) type = "event";
        else if (c.logic.has(word)) type = "logic";
        else if (afterThen && c.actions.has(word)) type = "action";
        else if (afterThen && c.chatTargets.has(word)) type = "target-" + (["youtube", "rumble", "kick"].includes(word) ? word : "chat");
        else if (afterThen && c.noticeTargets.has(word)) type = "target-notice";
        else if (c.operators.has(word) || word === c.isWord) type = "operator";
        else if (c.modulo.has(word)) type = "modulo";
        else if (c.fields.has(word)) type = "field";
        else if (c.events.has(word)) type = "event";
        else type = "word";
      } else if (piece.shape === "mark") {
        if (piece.text === "%") {
          const field = previous && previous.shape === "word" && c.fields.has(previous.text.toLowerCase());
          const numeric = next && next.shape === "number";
          type = field && numeric ? "modulo" : "filler";
        } else {
          type = next && next.shape === "number" ? "value" : "filler";
        }
      } else if (piece.shape === "time" || piece.shape === "number" || piece.shape === "value") {
        type = "value";
      } else if (piece.shape === "payload") {
        out.push(...payloadParts(piece, c));
        previous = piece;
        continue;
      } else if (piece.shape === "open") {
        type = "error";
      } else {
        type = "filler";
      }
      out.push({ type, text: piece.text });
      previous = piece;
    }
    return out;
  }

  // Fills a <pre> with one span per line, and the token spans in it. Text only, never HTML.
  function render(doc, pre, source, grammar, problems) {
    const c = grammar && grammar.events instanceof Set ? grammar : compile(grammar);
    const bad = problems || new Map();
    const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
    const fragment = doc.createDocumentFragment();
    lines.forEach((line, index) => {
      const row = doc.createElement("span");
      row.className = bad.has(index + 1) ? "blue-line blue-line-bad" : "blue-line";
      for (const token of tokenize(line, c)) {
        if (token.type === "space") { row.appendChild(doc.createTextNode(token.text)); continue; }
        const span = doc.createElement("span");
        span.className = "bt-" + token.type;
        span.textContent = token.text;
        row.appendChild(span);
      }
      fragment.appendChild(row);
      // A trailing newline still needs a line box, like the textarea gives it.
      fragment.appendChild(doc.createTextNode(index < lines.length - 1 ? "\n" : " "));
    });
    pre.replaceChildren(fragment);
    return lines.length;
  }

  // The raw pieces without spaces, for the test that compares them with blue.py.
  function shapes(line, grammar) {
    const c = grammar && grammar.events instanceof Set ? grammar : compile(grammar);
    return pieces(String(line), c)
      .filter((piece) => piece.shape !== "space")
      .map((piece) => ({ shape: piece.shape, text: piece.text }));
  }

  return { compile, tokenize, render, shapes, FALLBACK };
});
