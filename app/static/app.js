// BharatVoice call page: talk (or tap) to the shop and watch the parchi write itself.
const $ = (id) => document.getElementById(id);
const STARTERS = {
  "CUST-001": ["Do kilo atta aur ek litre Amul doodh", "Aashirvaad atta kitne ka hai?", "Pichli baar wala saaman bhej do"],
  "CUST-002": ["Ram Ram! do kilo cheeni aur ek darjan ande", "bees rupaye ka dhaniya", "mhara kitna baaki se?"],
  "CUST-003": ["दो किलो आशीर्वाद आटा भेज दीजिए", "पाव किलो जीरा और ढाई सौ ग्राम हल्दी", "अमूल गोल्ड है क्या?"],
};
let customer = "CUST-001", ws = null, vadObj = null, micOn = false;
let actx = null, nextTime = 0, sources = [], replyDone = true, carry = null, speaking = false;
let lines = new Map(), ended = false, lastAgent = "", askNext = false, outbound = false;

// ---------------------------------------------------------------- small helpers
function setState(state, label, status) {
  $("talk").dataset.state = state;
  if (label) $("talkLabel").textContent = label;
  if (status !== undefined) $("status").textContent = status;
}
function say(who, text, cls = "") {
  const p = document.createElement("p");
  if (cls) p.className = cls;
  p.innerHTML = `<b></b><span></span>`;
  p.querySelector("b").textContent = who;
  p.querySelector("span").textContent = text;
  $("convo").appendChild(p);
  $("convo").scrollTop = 1e9;
}
function chips(items) {
  const box = $("chips");
  box.innerHTML = "";
  (items || []).forEach((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = t;
    b.onclick = () => sendText(t);
    box.appendChild(b);
  });
  $("tryHead").textContent = ws ? "Tap to reply" : "Or tap a line to say it";
}
function qtyText(l) {
  if (l.unit === "kg" && l.qty < 1) return `${Math.round(l.qty * 1000)} g`;
  if (l.unit === "l" && l.qty < 1) return `${Math.round(l.qty * 1000)} ml`;
  const q = +l.qty.toFixed(3);
  if (l.unit === "pkt") return `${q} packet`;
  if (l.unit === "pc") return l.sku === "eggs" && q % 12 === 0 ? `${q / 12} darjan` : `${q}`;
  return `${q} ${{ kg: "kg", l: "litre" }[l.unit] ?? l.unit}`;
}
// write names the way a shopkeeper would: no pack sizes ("Surf Excel 1 kg" -> "Surf Excel")
const shortName = (n) => n.replace(/\s+\d+(\.\d+)?\s*(g|kg|ml|l)\b.*$/i, "").replace(/\s+\d+-pack.*$/i, "").replace(/^(Britannia|Parle) /, "");
const money = (x) => `₹${Math.round(x)}`;

// ---------------------------------------------------------------- the parchi
function resetParchi() {
  lines = new Map(); ended = false;
  $("lines").innerHTML = `<li class="empty"><span class="what">Order yahan likha jayega</span><span></span></li>`;
  $("totalline").hidden = true; $("question").textContent = ""; $("stamp").hidden = true; $("stamp").className = "stamp";
}
function renderCart(m) {
  const ol = $("lines");
  const empty = ol.querySelector(".empty");
  const seen = new Set();
  for (const l of m.cart) {
    seen.add(l.sku);
    const label = l.amount && l.qty && Math.abs(l.amount - l.qty * l.price) > 0.5 ? `${money(l.amount)} ka` : qtyText(l);
    const prev = lines.get(l.sku);
    if (!prev) {
      if (empty) empty.remove();
      const li = document.createElement("li");
      li.className = "new";
      li.innerHTML = `<span class="what"></span><span class="amt"></span>`;
      li.querySelector(".what").textContent = `${label} ${shortName(l.name)}`;
      if (l.via === "memory") { const n = document.createElement("span"); n.className = "recall"; n.textContent = "yaad tha"; li.querySelector(".what").appendChild(n); }
      li.querySelector(".amt").textContent = money(l.amount);
      ol.appendChild(li);
      lines.set(l.sku, { li, label });
    } else {
      prev.li.classList.remove("gone", "new");
      if (prev.label !== label) {  // a correction: strike the old quantity in red pen and write the new one
        const what = prev.li.querySelector(".what");
        what.innerHTML = `<s></s><span class="fix"></span> `;
        what.querySelector("s").textContent = prev.label;
        what.querySelector(".fix").textContent = label;
        what.append(shortName(l.name));
        prev.label = label;
      }
      prev.li.querySelector(".amt").textContent = money(l.amount);
    }
  }
  for (const [sku, v] of lines) if (!seen.has(sku)) v.li.classList.add("gone");
  $("total").textContent = money(m.total);
  $("totalline").hidden = m.cart.length === 0;
}
function stamp(kind) {
  const s = $("stamp");
  const text = { pakka: ["पक्का", "order confirmed"], udhaar: ["उधार", "written in the khata"], bhaiya: ["भैया", "shopkeeper will call back"] }[kind];
  s.innerHTML = `${text[0]}<small>${text[1]}</small>`;
  s.hidden = false; s.classList.remove("thump"); void s.offsetWidth; s.classList.add("thump");
  $("question").textContent = "";
}

// ---------------------------------------------------------------- audio out (streamed PCM16 16 kHz)
function stopAudio() { sources.forEach((s) => { try { s.stop(); } catch (e) {} }); sources = []; nextTime = 0; speaking = false; }
function audioDone() {
  if (!replyDone || sources.length) return;
  speaking = false;
  if (ended) return;
  if (micOn && vadObj) { vadObj.start(); setState("listening", "Listening, speak now", "Boliye. Tap Hang up to end the call."); }
  else setState("idle", ws ? "Speak instead" : "Call the shop", "");
}
function playChunk(buf) {
  let bytes = new Uint8Array(buf);
  if (carry) { const m = new Uint8Array(carry.length + bytes.length); m.set(carry); m.set(bytes, carry.length); bytes = m; carry = null; }
  if (bytes.length % 2) { carry = bytes.slice(-1); bytes = bytes.slice(0, -1); }
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2);
  if (!pcm.length || !actx) return;
  const ab = actx.createBuffer(1, pcm.length, 16000), ch = ab.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = actx.createBufferSource(); src.buffer = ab; src.connect(actx.destination);
  nextTime = Math.max(nextTime, actx.currentTime + 0.03); src.start(nextTime); nextTime += ab.duration;
  sources.push(src);
  src.onended = () => { sources = sources.filter((x) => x !== src); audioDone(); };
}
function wav(samples, sr = 16000) {
  const b = new ArrayBuffer(44 + samples.length * 2), v = new DataView(b);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([b], { type: "audio/wav" });
}

// ---------------------------------------------------------------- the call
function connect() {
  return new Promise((resolve, reject) => {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    actx.resume();
    resetParchi(); $("convo").innerHTML = ""; $("timing").textContent = "";
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/call?customer=${customer}${outbound ? "&outbound=1" : ""}`);
    outbound = false;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { $("hang").hidden = false; resolve(); };
    ws.onerror = () => reject(new Error("connection failed"));
    ws.onclose = () => { ws = null; micOn = false; if (vadObj) { vadObj.destroy(); vadObj = null; } $("hang").hidden = true;
      if (!ended) setState("idle", "Call the shop", "Call ended."); chips(STARTERS[customer]); };
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) { playChunk(ev.data); return; }
      const m = JSON.parse(ev.data);
      if (m.type === "transcript") { say("Aap", m.text || "(kuch sunai nahi diya)"); setState("thinking", "Writing it down", ""); }
      else if (m.type === "reply_start") {
        lastAgent = m.text; say("Dukaan", m.text); replyDone = false; speaking = true;
        $("question").textContent = askNext && !ended ? m.text : "";
        if (vadObj && micOn) vadObj.pause();
        setState("speaking", "Shop is speaking", "");
      }
      else if (m.type === "reply_end") {
        replyDone = true;
        // if audio can't play (blocked autoplay, suspended context), don't leave the call stuck on "speaking"
        const left = actx && actx.state === "running" ? Math.max(0, nextTime - actx.currentTime) : 0;
        setTimeout(() => { if (speaking) { sources = []; audioDone(); } }, (left + 0.6) * 1000);
        audioDone();
      }
      else if (m.type === "cart") {
        renderCart(m);
        askNext = Boolean(m.pending || m.awaiting_confirm);  // the reply that follows is a question to the caller
        chips(m.suggestions);
      }
      else if (m.type === "suggest") chips(m.items);
      else if (m.type === "timings" && m.stt_ms && m.to_first_audio_ms) $("timing").textContent = `Heard you and replied in ${(m.to_first_audio_ms / 1000).toFixed(1)} s`;
      else if (m.type === "error") say("Note", m.text, "err");
      else if (m.type === "end") {
        ended = true;
        stamp(m.handoff ? "bhaiya" : (m.order && m.order.payment === "udhaar" ? "udhaar" : "pakka"));
        chips(STARTERS[customer]); $("tryHead").textContent = "Try another order"; $("hang").hidden = true;
        setState("idle", "Call again", m.order ? `Order ${m.order.order_id} is on the shop counter.` : "The shopkeeper will call you back.");
        if (vadObj) vadObj.pause();
      }
    };
  });
}
async function startMic() {
  if (typeof vad === "undefined") throw new Error("voice detector not loaded");
  vadObj = await vad.MicVAD.new({
    baseAssetPath: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/",
    onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/",
    positiveSpeechThreshold: 0.6, redemptionMs: 650, minSpeechMs: 250,  // 650 ms: callers pause after "Bhaiya,"
    onSpeechStart: () => { if (speaking) stopAudio(); setState("listening", "Listening", "Sun rahe hain…"); },
    onSpeechEnd: (audio) => { if (ws && ws.readyState === 1) { ws.send(wav(audio)); setState("thinking", "Writing it down", ""); } },
  });
  micOn = true;
}
async function call() {
  if (ws && micOn) return;
  setState("thinking", "Connecting", "");
  try {
    if (!ws) await connect();
    await startMic();
    // the greeting may already have finished while the voice detector loaded: then start listening right away
    if (speaking || !replyDone) setState("speaking", "Shop is speaking", ""); else audioDone();
  } catch (e) {
    micOn = false;
    setState("idle", ws ? "Speak instead" : "Call the shop",
      "Microphone is off or blocked. You can still order by tapping a line or typing below.");
  }
}
async function sendText(text) {
  if (!text.trim()) return;
  if (ended || !ws) { try { await connect(); } catch (e) { $("status").textContent = "Couldn't reach the shop. Try again in a moment."; return; } }
  const go = () => { ws.send(JSON.stringify({ type: "text", text })); setState("thinking", "Writing it down", ""); };
  if (lastAgent) go(); else setTimeout(go, 1200);  // let the greeting start first
}

$("talk").onclick = () => { if (ended && ws) { ws.close(); } call(); };
$("hang").onclick = () => { if (ws) ws.close(); stopAudio(); };
$("typed").onsubmit = (e) => { e.preventDefault(); const t = $("typedText").value; $("typedText").value = ""; sendText(t); };
document.querySelectorAll(".who button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".who button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  customer = b.dataset.cust; if (ws) ws.close(); stopAudio(); resetParchi(); lastAgent = ""; chips(STARTERS[customer]);
});
$("today").textContent = new Date().toLocaleDateString("en-IN", { day: "numeric", month: "short" });
chips(STARTERS[customer]);
fetch("/api/config").then((r) => r.json()).then((c) => { if (c.public_demo) $("notice").hidden = false; }).catch(() => {});

// ---------------------------------------------------------------- the shop calls you (/?outbound=CUST-001)
const ob = new URLSearchParams(location.search).get("outbound");
if (ob && STARTERS[ob]) {
  customer = ob;
  document.querySelectorAll(".who button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.cust === ob)));
  chips(STARTERS[ob]);
  const card = document.getElementById("incoming");
  card.hidden = false;
  card.querySelector(".who-calls").textContent = { "CUST-001": "Sharma ji", "CUST-002": "Tau ji", "CUST-003": "Gupta ji" }[ob];
  document.getElementById("pickup").onclick = async () => {
    card.hidden = true; outbound = true;
    await call();  // picking up opens the microphone, like answering a real phone
  };
  document.getElementById("decline").onclick = () => { card.hidden = true; history.replaceState(null, "", "/"); };
}

// ---------------------------------------------------------------- learning-curve chart (questions per call)
(function curve() {
  const svg = document.getElementById("curve");
  if (!svg) return;
  const D = window.LEARNING || { off: [3.0, 3.67, 3.0, 4.0, 3.0], on: [3.0, 2.0, 0.33, 1.33, 0.33], caption: "" };
  const W = 520, H = 260, L = 40, R = 90, T = 16, B = 36, ymax = 4.5;
  const PAD = 18;  // keep the first point off the y-axis labels
  const x = (i) => L + PAD + i * (W - L - R - PAD) / (D.on.length - 1), y = (v) => T + (1 - v / ymax) * (H - T - B);
  const ns = "http://www.w3.org/2000/svg", el = (t, a) => { const e = document.createElementNS(ns, t); for (const k in a) e.setAttribute(k, a[k]); svg.appendChild(e); return e; };
  for (let v = 0; v <= 4; v++) { el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }); el("text", { x: L - 10, y: y(v) + 4, "text-anchor": "end", class: "axis" }).textContent = v; }
  D.on.forEach((_, i) => { el("text", { x: x(i), y: H - 12, "text-anchor": "middle", class: "axis" }).textContent = `Call ${i + 1}`; });
  const tip = el("g", { class: "tipbox", visibility: "hidden" });
  const tipBg = document.createElementNS(ns, "rect"); tipBg.setAttribute("rx", 6); tipBg.setAttribute("fill", "#18224f"); tip.appendChild(tipBg);
  const tipTx = document.createElementNS(ns, "text"); tipTx.setAttribute("fill", "#fdfeff"); tipTx.setAttribute("font-size", "12"); tip.appendChild(tipTx);
  for (const [k, name] of [["off", "Starts fresh"], ["on", "Remembers"]]) {
    el("path", { d: D[k].map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" "), class: `line ${k}` });
    D[k].forEach((v, i) => {
      el("circle", { cx: x(i), cy: y(v), r: 5, class: k });
      const h = el("circle", { cx: x(i), cy: y(v), r: 14, class: "hit", tabindex: 0 });
      const show = () => { tipTx.textContent = `${name}, call ${i + 1}: ${v.toFixed(1)} questions`; tip.setAttribute("visibility", "visible");
        const w = tipTx.getComputedTextLength() + 16; tipBg.setAttribute("width", w); tipBg.setAttribute("height", 24);
        const tx = Math.min(x(i) - w / 2, W - w), ty = y(v) - 36; tipBg.setAttribute("x", tx); tipBg.setAttribute("y", ty); tipTx.setAttribute("x", tx + 8); tipTx.setAttribute("y", ty + 16); };
      h.addEventListener("mouseenter", show); h.addEventListener("focus", show);
      h.addEventListener("mouseleave", () => tip.setAttribute("visibility", "hidden")); h.addEventListener("blur", () => tip.setAttribute("visibility", "hidden"));
    });
    const last = D[k].length - 1;
    el("text", { x: x(last) + 10, y: y(D[k][last]) + 4, class: "lbl" }).textContent = `${D[k][last].toFixed(1)} ${k === "on" ? "remembers" : "fresh"}`;
  }
  svg.appendChild(tip);
  document.getElementById("curveDesc").textContent = `Questions per call over five calls. Starts fresh: ${D.off.join(", ")}. Remembers: ${D.on.join(", ")}.`;
  document.getElementById("curveCap").textContent = D.caption;
})();
