/* Credential-value-only display policy. Names, endpoints and counters survive. */
(() => {
  'use strict';
  const name = 'password|passwd|pwd|token|secret|credentials?|authorization|private[_ .-]?key|api[_ .-]?key|access[_ .-]?key|client[_ .-]?(?:key|secret)|(?:access|refresh|auth|authentication|bearer|api)[_ .-]?token';
  const literal = String.raw`\\"(?:(?!\\")[\s\S])*\\"|\\'(?:(?!\\')[\s\S])*\\'|"(?:\\.|""|[^"\\])*"|'(?:\\.|''|[^'\\])*'`;
  const value = String.raw`\[REDACTED\]|(?:${literal})(?:\s*\+\s*(?:${literal}))*|[^\s,;\)\]}&"']+`;
  const lookup = String.raw`(?:\$\$?[A-Za-z_]|dbutils\.secrets\.get\s*\(|(?:os\.)?(?:getenv|environ)\b|System\.getenv\s*\()`;
  const pattern = new RegExp(
    String.raw`(?<material>\b(?:dapi[a-z0-9]{20,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|lsv2_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b|-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----)|` +
    String.raw`(?<bearer>\bBearer\s+)(?<bearer_value>[^\s"',;]+)|` +
    String.raw`(?<url>[a-z][a-z0-9+.-]*://[^\s/@:]+:)(?<url_value>[^\s/@]+)(?=@)|` +
    String.raw`(?<assignment>(?<![\w])(?:[A-Za-z0-9]+[_ .-])?(?:${name})["']?\s*[:=]\s*)(?<assignment_value>(?!${lookup})(?:(?:Basic|Bearer)\s+)?(?:${value}))|` +
    String.raw`(?<flag>--(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\s+)(?<flag_value>(?!${lookup})(?:${value}))|` +
    String.raw`(?<identified>\bIDENTIFIED\s+BY\s+)(?<identified_value>${value})|` +
    String.raw`(?<setter>\bset(?:Password|Token|Secret|ApiKey|AccessToken|RefreshToken|PrivateKey)\s*\(\s*)(?<setter_value>(?!${lookup})(?:${value}))|` +
    String.raw`(?<put>\b(?:put|setProperty)\s*\(\s*["'](?:${name})["']\s*,\s*)(?<put_value>(?!${lookup})(?:${value}))`, 'gi');
  const text = input => String(input).replace(pattern, (...args) => {
    const groups = args[args.length - 1];
    if (groups.material !== undefined) return '[REDACTED]' + '\n'.repeat((args[0].match(/\n/g) || []).length);
    for (const group of ['bearer', 'url', 'assignment', 'flag', 'identified', 'setter', 'put']) {
      if (groups[group] === undefined) continue;
      const raw = groups[group + '_value'];
      const quote = /^\\["']/.test(raw) ? raw.slice(0, 2) : /^["']/.test(raw) ? raw[0] : '';
      return groups[group] + quote + '[REDACTED]' + '\n'.repeat((raw.match(/\n/g) || []).length) + quote;
    }
    return '[REDACTED]';
  });
  window.orionRedaction = Object.freeze({text});
})();