/**
 * The server an agentgateway name belongs to. The gateway names a tool `<target>_<tool>` and a resource URI
 * `<target>+<scheme>://...`. A target name can hold the separator too, so the configured targets are matched
 * first (longest first), then the text before the first separator.
 */
export function serverOf(key: string, separator: string, targets: string[]): string {
  const known = [...targets].sort((a, b) => b.length - a.length)
  const index = key.indexOf(separator)
  return known.find((target) => key.startsWith(target + separator)) ?? (index > 0 ? key.slice(0, index) : '')
}
