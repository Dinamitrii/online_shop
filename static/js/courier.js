(() => {
  const form = document.getElementById('courier-form');
  if (!form) return;
  const delivery = document.getElementById('delivery_type');
  const updateFields = () => {
    const office = delivery.value === 'office';
    document.getElementById('office-fields').hidden = !office;
    document.getElementById('address-fields').hidden = office;
    document.getElementById('receiver_office').required = office;
    for (const id of ['city', 'post_code', 'address']) document.getElementById(id).required = !office;
  };
  delivery.addEventListener('change', updateFields);
  updateFields();
  const button = document.getElementById('load-offices');
  button.addEventListener('click', async () => {
    const status = document.getElementById('offices-status');
    button.disabled = true;
    status.textContent = 'Зареждане на офисите…';
    try {
      const response = await fetch(button.dataset.url, {headers: {'Accept': 'application/json'}});
      if (response.redirected) throw new Error('Влезте отново в админ панела.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Неуспешно зареждане.');
      const list = document.getElementById('econt-offices');
      list.replaceChildren();
      for (const office of data.offices) {
        const option = document.createElement('option');
        option.value = office.code;
        option.label = `${office.city} · ${office.name}`;
        list.append(option);
      }
      status.textContent = `Заредени ${data.offices.length} офиса. Изберете офис по име или код.`;
    } catch (error) {
      status.textContent = error.message || 'Неуспешно зареждане. Опитайте отново.';
    } finally {
      button.disabled = false;
    }
  });
  let submitting = false;
  form.addEventListener('submit', event => {
    if (submitting) {event.preventDefault(); return;}
    submitting = true;
    // Keep submitter enabled so its action value remains in the POST body.
    form.setAttribute('aria-busy', 'true');
  });
})();
