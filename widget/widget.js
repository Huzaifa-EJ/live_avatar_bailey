/**
 * BizApp247 avatar demo widget.
 *
 * Flow:
 *  1. Visitor clicks "Start the live demo"
 *  2. Fetch a LiveKit token from the API, connect, subscribe to the
 *     avatar's video/audio tracks, enable the visitor's mic
 *  3. Send { event: "session_started" } -> avatar delivers the welcome
 *  4. Each form field, on blur/change with a valid-enough value, sends
 *     { event: "field_completed", field, value } -> avatar guides to next
 *  5. Submit -> POST /api/lead -> send { event: "form_submitted" } ->
 *     let the avatar say goodbye -> redirect to the demo page
 */

import {
  Room,
  RoomEvent,
  Track,
} from "https://cdn.jsdelivr.net/npm/livekit-client@2/+esm";

const API_BASE = window.BIZAPP_API_BASE || "https://api.yourdomain.com";
const FIELDS = ["first_name", "last_name", "email", "phone", "business_name", "industry"];
const GOODBYE_GRACE_MS = 6000; // let the avatar finish its goodbye before redirect

const $ = (id) => document.getElementById(id);
const encoder = new TextEncoder();

let room = null;
let connected = false;
const announced = new Set(); // fields the agent has been told about

// ---------------------------------------------------------------- helpers

function clientId() {
  const key = "bizapp_client_id";
  let id = localStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(key, id);
  }
  return id;
}

function validate(field, value) {
  const v = value.trim();
  switch (field) {
    case "email":
      return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
    case "phone":
      return v.replace(/\D/g, "").length >= 7;
    default:
      return v.length > 0;
  }
}

function sendToAgent(payload) {
  if (!connected || !room) return;
  room.localParticipant.publishData(
    encoder.encode(JSON.stringify(payload)),
    { reliable: true, topic: "form" }
  );
}

function setStepState(field, state) {
  const step = document.querySelector(`.rail-step[data-step="${field}"]`);
  if (!step) return;
  step.classList.remove("active", "done", "invalid");
  if (state) step.classList.add(state);
}

function refreshSubmit() {
  const allValid = FIELDS.every((f) => validate(f, $(f).value));
  $("btnSubmit").disabled = !allValid;
}

// ---------------------------------------------------------------- LiveKit

async function startSession() {
  const btn = $("btnStart");
  btn.disabled = true;
  btn.textContent = "Connecting…";

  try {
    const resp = await fetch(`${API_BASE}/api/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId() }),
    });
    if (!resp.ok) throw new Error(`token ${resp.status}`);
    const { url, token } = await resp.json();

    room = new Room({ adaptiveStream: true });

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Video) {
        track.attach($("avatarVideo"));
      } else if (track.kind === Track.Kind.Audio) {
        const el = track.attach();
        el.hidden = true;
        document.body.appendChild(el);
      }
    });

    room.on(RoomEvent.Disconnected, () => {
      connected = false;
    });

    // Optional live captions if the agent publishes transcriptions
    room.registerTextStreamHandler?.("lk.transcription", async (reader) => {
      const text = await reader.readAll();
      $("captions").textContent = text;
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    connected = true;

    $("stageIdle").classList.add("hidden");
    $("stageLive").classList.remove("hidden");
    setStepState("first_name", "active");
    $("first_name").focus();

    // Small delay so the avatar track is up before it starts talking
    setTimeout(() => sendToAgent({ event: "session_started" }), 800);
  } catch (err) {
    console.error(err);
    btn.disabled = false;
    btn.textContent = "Start the live demo";
    $("formStatus").textContent =
      "Couldn't start the live session — the form below still works.";
    // Graceful degrade: form remains usable without the avatar.
  }
}

// ---------------------------------------------------------------- form

function announceField(field) {
  const value = $(field).value.trim();
  if (!value || announced.has(field)) return;

  const valid = validate(field, value);
  if (!valid) {
    setStepState(field, "invalid");
    // Tell the agent so Bailey can nudge verbally (email/phone only)
    if (field === "email" || field === "phone") {
      sendToAgent({ event: "field_completed", field, value: "", valid: false });
    }
    refreshSubmit();
    return;
  }

  announced.add(field);
  setStepState(field, "done");
  const next = FIELDS[FIELDS.indexOf(field) + 1];
  if (next && !announced.has(next)) setStepState(next, "active");

  sendToAgent({ event: "field_completed", field, value, valid: true });
  refreshSubmit();
}

function wireForm() {
  for (const field of FIELDS) {
    const el = $(field);
    const evt = el.tagName === "SELECT" ? "change" : "blur";
    el.addEventListener(evt, () => announceField(field));
    el.addEventListener("input", () => {
      // re-validate if they fix a flagged field
      if (el.closest(".rail-step").classList.contains("invalid") &&
          validate(field, el.value)) {
        setStepState(field, "active");
      }
      refreshSubmit();
    });
  }

  $("leadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("btnSubmit");
    btn.disabled = true;
    btn.textContent = "Setting up your demo…";
    $("formStatus").classList.remove("error");
    $("formStatus").textContent = "";

    const lead = Object.fromEntries(FIELDS.map((f) => [f, $(f).value.trim()]));

    try {
      const resp = await fetch(`${API_BASE}/api/lead`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lead),
      });
      if (!resp.ok) throw new Error(`lead ${resp.status}`);
      const { redirect_url } = await resp.json();

      sendToAgent({ event: "form_submitted", lead: { first_name: lead.first_name } });
      $("formStatus").textContent = "Done! Taking you to your demo…";

      setTimeout(async () => {
        try { await room?.disconnect(); } catch {}
        window.location.assign(redirect_url);
      }, connected ? GOODBYE_GRACE_MS : 400);
    } catch (err) {
      console.error(err);
      btn.disabled = false;
      btn.textContent = "Submit & open my demo";
      $("formStatus").classList.add("error");
      $("formStatus").textContent = "Something went wrong sending your info. Please try again.";
    }
  });
}

// ---------------------------------------------------------------- boot

$("btnStart").addEventListener("click", startSession);
wireForm();
window.addEventListener("beforeunload", () => room?.disconnect());
