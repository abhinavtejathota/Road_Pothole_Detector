export function nationalMobileDigits(raw) {
  let digits = String(raw || '').replace(/\D/g, '');
  while (digits.length > 10) {
    if (digits.startsWith('0091') && digits.length >= 13) digits = digits.slice(4);
    else if (digits.startsWith('91') && digits.length >= 12) digits = digits.slice(2);
    else if (digits.startsWith('0') && digits.length === 11) digits = digits.slice(1);
    else break;
  }
  return digits.slice(0, 10);
}

export function isValidIndianMobile(digits) {
  return /^[6-9]\d{9}$/.test(digits || '');
}

export function formatIndianMobile(digits) {
  return digits ? `+91 ${digits}` : '+91';
}
