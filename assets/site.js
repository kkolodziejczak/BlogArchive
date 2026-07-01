(function () {
  const search = document.querySelector("#archive-search");
  const items = Array.from(document.querySelectorAll("[data-search]"));

  if (!search || items.length === 0) {
    return;
  }

  search.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    for (const item of items) {
      item.classList.toggle("hidden", query !== "" && !item.dataset.search.includes(query));
    }
  });
})();
