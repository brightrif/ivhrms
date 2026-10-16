/* Phone boxes: choosing a country in the code list updates the flag, the code shown and the value that is sent. */
document.addEventListener("click", function (e) {
  var item = e.target.closest("[data-phone-pick]");
  if (!item) {
    return;
  }
  var box = item.closest("[data-phone]");
  box.querySelector("[data-phone-code]").value = item.dataset.code;
  box.querySelector("[data-phone-flag]").className =
    "fi fi-" + item.dataset.iso + " border";
  box.querySelector("[data-phone-dial]").textContent = "+" + item.dataset.code;
});
