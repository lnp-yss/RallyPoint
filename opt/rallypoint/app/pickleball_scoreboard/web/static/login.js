"use strict";
// Select the isolated login page before credentials are entered.
const clubSelector = document.querySelector("#login-club");
if (clubSelector) clubSelector.addEventListener("change", () => {
    const destination = new URL(clubSelector.value, location.origin);
    if (destination.origin === location.origin) location.assign(destination.href);
});
