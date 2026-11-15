// js/calendar.js — entry point (formerly app.js).
//
// Boot sequence:
//   1. UI modules render synchronously from this device's localStorage cache,
//      so the calendar paints before the network answers.
//   2. initialSync() adopts the shared snapshot from the hub.
//   3. startPolling() watches the version endpoint every 5s (+ on
//      focus/visibility/pageshow) and pulls changes made on other devices.
//   4. initDayRoll() repaints when the local day changes (midnight / refocus),
//      so an app left open overnight shows the correct "today".
//
// There is no login gate and no session: the hub is open to everyone who can
// reach it, and all calendar data is family-shared.

import { initSidebar } from "./sidebar.js";
import { initGrid } from "./grid.js";
import { initModal } from "./modal.js";
import { initEventDetails } from "./eventDetails.js";
import { initDayModal } from "./dayModal.js";
import { initUpcoming } from "./upcoming.js";
import { initCountdown } from "./countdown.js";
import { initDayRoll } from "./dayRoll.js";

import { getState, getServerVersion, replaceServerData } from "./state.js";
import { initialSync, startPolling } from "./sync.js";

document.addEventListener("DOMContentLoaded", () => {
  // Order is significant: modal must exist before grid (grid opens it on
  // cell click).
  initModal();
  initDayModal();
  initEventDetails();
  initGrid();
  initSidebar();
  initCountdown();
  initUpcoming();
  initDayRoll();

  initialSync(replaceServerData);

  startPolling({
    getLocalVersion: getServerVersion,
    onRemoteChange: replaceServerData,
  });
});
