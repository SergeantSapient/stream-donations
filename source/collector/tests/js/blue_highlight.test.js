// The BLUE editor's tokenizer, against blue.py. Run: node --test source/collector/tests/js/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");

const BlueHighlight = require(path.join(__dirname, "..", "..", "streamdonations", "web", "blue_highlight.js"));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "blue_fixture.json"), "utf8"));
const grammar = BlueHighlight.compile(fixture.grammar);

// blue.py drops the quotes and undoes the backslashes.
function unquote(text) {
  return text.slice(1, -1).replace(/\\(.)/g, "$1");
}

const SAME = {
  payload: ["payload"],
  value: ["value", "time", "number"],
  word: ["word", "mark"],
  keyword: ["word"],
};

test("splits each line where blue.py does", () => {
  for (const { line, tokens } of fixture.lines) {
    const shapes = BlueHighlight.shapes(line, grammar).filter((piece) => piece.shape !== "other");
    assert.equal(shapes.length, tokens.length, line);
    shapes.forEach((piece, index) => {
      const [kind, text] = tokens[index];
      assert.ok(SAME[kind].includes(piece.shape), `${line}: ${piece.text} is ${piece.shape}, blue.py says ${kind}`);
      const mine = piece.shape === "payload" || (piece.shape === "value" && /^['"]/.test(piece.text))
        ? unquote(piece.text) : piece.text;
      assert.equal(mine, text, line);
      if (kind === "keyword") assert.ok(fixture.grammar.keywords.includes(text.toLowerCase()), text);
    });
  }
});

test("keeps every character", () => {
  for (const { line } of fixture.lines) {
    assert.equal(BlueHighlight.tokenize(line, grammar).map((token) => token.text).join(""), line);
  }
  const odd = 'ON DONATION  THEN\tSEND "open';
  assert.equal(BlueHighlight.tokenize(odd, grammar).map((token) => token.text).join(""), odd);
});

function types(line) {
  return BlueHighlight.tokenize(line, grammar)
    .filter((token) => token.type !== "space")
    .map((token) => token.type + ":" + token.text);
}

test("colors words by where they are", () => {
  assert.deepEqual(
    types('ON DONATION IF PLATFORM IS KICK THEN SEND CHAT TO PLATFORM AND KICK "x"'),
    [
      "logic:ON", "event:DONATION", "logic:IF", "field:PLATFORM", "operator:IS", "word:KICK", "logic:THEN",
      "action:SEND", "action:CHAT", "action:TO", "target-chat:PLATFORM", "logic:AND", "target-kick:KICK",
      'payload:"x"',
    ],
  );
  assert.deepEqual(types("on timer then send notice to app"), [
    "logic:on", "event:timer", "logic:then", "action:send", "action:notice", "action:to", "target-notice:app",
  ]);
});

test("% is MODULO only after a field and before a number, and - is a sign", () => {
  assert.deepEqual(types("IF AMOUNT % 5"), ["logic:IF", "field:AMOUNT", "modulo:%", "value:5"]);
  assert.deepEqual(types("IF 50 % off"), ["logic:IF", "value:50", "filler:%", "word:off"]);
  assert.deepEqual(types("MINUTES MOD - 5"), ["field:MINUTES", "modulo:MOD", "value:-", "value:5"]);
  assert.deepEqual(types("IS - THEN"), ["operator:IS", "filler:-", "logic:THEN"]);
});

test("% is MODULO where blue.py reads it so, past filler, NOT, and minus signs", () => {
  const modulo = (line) => types(line).filter((item) => item.endsWith(":%"));
  for (const line of [
    "IF AMOUNT IS % 5 EQUALS 0",
    "IF AMOUNT NOT % 5 EQUALS 0",
    "IF TIME IS % 15 EQUALS 0",
    "IF AMOUNT the remainder % 10 EQUALS 0",
    "IF AMOUNT % -5 EQUALS 0",
    "IF AMOUNT % - 5 EQUALS 0",
    "IF NOT AMOUNT % 4 EQUALS 0",
    "IF RAW AMOUNT % 100 EQUALS 0",
  ]) {
    assert.deepEqual(modulo(line), ["modulo:%"], line);
  }
  for (const line of [
    "IF AMOUNT IS ABOVE % 5",
    "IF 50 % off",
    "IF AMOUNT MOD 5 % 3 EQUALS 0",
    'THEN SEND CHAT TO ALL "x" AMOUNT % 5',
    "IF AMOUNT % THEN",
  ]) {
    assert.deepEqual(modulo(line), ["filler:%"], line);
  }
});

test("placeholders the host fills in, and ones it does not", () => {
  const parts = types('THEN SEND CHAT TO ALL "a {MENTION} {raw_amount} {TotalBlurb} {BOGUS}"');
  assert.ok(parts.includes("placeholder:{MENTION}"));
  assert.ok(parts.includes("placeholder:{raw_amount}"));
  assert.ok(parts.includes("placeholder:{TotalBlurb}"));
  assert.ok(parts.includes("placeholder-unknown:{BOGUS}"));
});

test("an open quote is marked", () => {
  assert.deepEqual(types('THEN SEND "open'), ["logic:THEN", "action:SEND", 'error:"open']);
});

test("render builds spans with the line marks and no HTML", () => {
  const made = [];
  const node = (tag) => ({ tag, className: "", textContent: "", children: [], appendChild(child) { this.children.push(child); } });
  const doc = {
    createDocumentFragment: () => node("#fragment"),
    createElement: (tag) => { const item = node(tag); made.push(item); return item; },
    createTextNode: (text) => ({ tag: "#text", textContent: text }),
  };
  const pre = { replaceChildren(fragment) { this.children = fragment.children; } };
  const count = BlueHighlight.render(doc, pre, 'ON DONATION THEN SEND NOTICE TO APP "<b>"\nON BAD', grammar, new Map([[2, "x"]]));
  assert.equal(count, 2);
  const rows = pre.children.filter((child) => child.tag === "span");
  assert.deepEqual(rows.map((row) => row.className), ["blue-line", "blue-line blue-line-bad"]);
  assert.ok(made.some((item) => item.className === "bt-payload" && item.textContent === '"<b>"'));
});

test("a problem mark stays only while its line is unchanged", () => {
  const checked = BlueHighlight.lines("ON DONATION THEN\nON BAD LINE\nON TIMER THEN");
  const problems = new Map([[2, "That line has no action."]]);
  assert.deepEqual([...BlueHighlight.currentProblems(problems, checked, checked.join("\n"))], [[2, "That line has no action."]]);
  // A line inserted above: line 2 is now different text, so no mark points at it.
  assert.deepEqual([...BlueHighlight.currentProblems(problems, checked, "NEW\nON DONATION THEN\nON BAD LINE")], []);
  // The line itself was edited.
  assert.deepEqual([...BlueHighlight.currentProblems(problems, checked, "ON DONATION THEN\nON BAD LINES\n")], []);
  // Deleted.
  assert.deepEqual([...BlueHighlight.currentProblems(problems, checked, "ON DONATION THEN")], []);
  // Nothing checked yet.
  assert.deepEqual([...BlueHighlight.currentProblems(new Map(), [], "x")], []);
});
