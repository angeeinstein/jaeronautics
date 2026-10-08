/**
 * Files the API answers with (a PDF, an export): their name as the server
 * gave it, and saving or showing one the browser already holds.
 */

/** The file's name from the answer's Content-Disposition, or ``fallback``. */
export function fileNameOf(response: Response | undefined, fallback: string): string {
  const disposition = response?.headers.get('Content-Disposition') ?? '';
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1];
  return name ? decodeURIComponent(name) : fallback;
}

/** Save ``file`` under ``name``, as a download. */
export function saveFile(file: Blob, name: string) {
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  // Long enough for the download to have read it.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 60_000);
}

/** Show ``file`` in a new tab where the browser allows one, save it otherwise. */
export function openFile(file: Blob, name: string) {
  const url = URL.createObjectURL(file);
  const tab = window.open(url, '_blank');
  if (!tab) {
    URL.revokeObjectURL(url);
    saveFile(file, name);
    return;
  }
  tab.opener = null;
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 60_000);
}
