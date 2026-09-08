(() => {
  const query = document.querySelector<HTMLInputElement>("#sample-query");
  const override = document.querySelector<HTMLSelectElement>("#override-filter");
  const rows = document.querySelectorAll<HTMLTableRowElement>("tbody tr");
  if (!query || !override) return;

  function filter(): void {
    const wanted = query?.value.trim().toLocaleLowerCase() ?? "";
    const overrideValue = override?.value ?? "all";
    for (const row of rows) {
      const matchesText = (row.dataset.search ?? "").includes(wanted);
      const matchesOverride =
        overrideValue === "all" || row.dataset.override === overrideValue;
      row.hidden = !(matchesText && matchesOverride);
    }
  }

  query.addEventListener("input", filter);
  override.addEventListener("change", filter);
})();
