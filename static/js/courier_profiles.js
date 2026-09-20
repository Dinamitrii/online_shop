(() => {
  const root = document.getElementById('sender-profiles');
  if (!root) return;
  const button = document.getElementById('load-profiles');
  const select = document.getElementById('profile-select');
  const addresses = document.getElementById('profile-address');
  const status = document.getElementById('profiles-status');
  const senderType = document.getElementById('sender_type');
  const pickup = root.dataset.mode === 'pickup';
  let profiles = [];
  const set = (id, value) => { const field = document.getElementById(id); if (field) field.value = value || ''; };
  const updateFields = () => {
    if (!senderType) return;
    const fromAddress = senderType.value === 'address';
    document.getElementById('sender-address-fields').hidden = !fromAddress;
    const officeFields = document.getElementById('sender-office-fields');
    if (officeFields) officeFields.hidden = fromAddress;
    document.getElementById('sender_office').required = !fromAddress;
    for (const id of ['sender_city', 'sender_post_code', 'sender_address']) document.getElementById(id).required = fromAddress;
  };
  if (senderType) {senderType.addEventListener('change', updateFields); updateFields();}
  const resetAddresses = () => {
    addresses.replaceChildren(new Option('Изберете адрес', ''));
    addresses.disabled = true;
  };
  select.addEventListener('change', () => {
    resetAddresses();
    if (select.value === '') return;
    const profile = profiles[Number(select.value)];
    if (!pickup) {
      set('sender_name', profile.name);
      set('sender_phone', profile.phones[0]);
      // An address from another profile must not remain selected for this sender:
      // fall back to the shop address from .env (empty if it is not configured).
      set('sender_city', root.dataset.shopCity);
      set('sender_post_code', root.dataset.shopPostCode);
      set('sender_address', root.dataset.shopAddress);
    }
    profile.addresses.forEach((a, i) => addresses.append(new Option(`${a.city} ${a.post_code} · ${a.address}`, String(i))));
    addresses.disabled = !profile.addresses.length;
    status.textContent = profile.addresses.length ? 'Изберете запазен адрес или офис за изпращане.' : 'Профилът няма запазен адрес в България. Можете да го въведете ръчно.';
  });
  addresses.addEventListener('change', () => {
    if (select.value === '' || addresses.value === '') return;
    const address = profiles[Number(select.value)].addresses[Number(addresses.value)];
    const prefix = pickup ? '' : 'sender_';
    for (const key of ['city', 'post_code', 'address']) set(prefix + key, address[key]);
    if (senderType) {senderType.value = 'address'; updateFields();}
    status.textContent = 'Адресът е попълнен от Еконт. Проверете данните преди изпращане.';
  });
  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = 'Зареждане на профилите от Еконт…';
    select.disabled = true; select.replaceChildren(new Option('Изберете профил', '')); resetAddresses();
    try {
      const response = await fetch(root.dataset.url, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (response.redirected) throw new Error('Влезте отново в админ панела.');
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Неочакван отговор от сървъра. Опитайте отново.'); }
      if (!response.ok) throw new Error(data.error || 'Неуспешно зареждане от Еконт.');
      profiles = data.profiles;
      if (pickup) profiles = profiles.filter(p => p.name === root.dataset.sender);
      profiles.forEach((p, i) => select.append(new Option(`${p.name} · ${p.phones.join(', ')}`, String(i))));
      select.disabled = !profiles.length;
      status.textContent = profiles.length ? 'Изберете профила на подателя.' : 'Няма подходящ профил в този акаунт. Можете да въведете данните ръчно.';
    } catch (error) {
      status.textContent = error.message || 'Неуспешно зареждане от Еконт.';
    } finally {button.disabled = false;}
  });
})();
