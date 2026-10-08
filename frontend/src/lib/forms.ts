/** An empty box sends nothing: "" becomes null, as the API keeps "not given". */
export function emptyToNull(text: string | null | undefined): string | null {
  if (text === undefined || text === null || text.trim() === '') return null;
  return text;
}
