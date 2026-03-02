/**
 * Export an array of objects to a CSV file and trigger download.
 */
export function exportToCsv(
  filename: string,
  rows: Record<string, unknown>[],
  columns?: { key: string; label: string }[],
) {
  if (rows.length === 0) return

  const cols = columns || Object.keys(rows[0]).map((k) => ({ key: k, label: k }))
  const header = cols.map((c) => c.label).join(',')

  const body = rows
    .map((row) =>
      cols
        .map((c) => {
          const val = row[c.key]
          if (val == null) return ''
          const str = typeof val === 'object' ? JSON.stringify(val) : String(val)
          if (str.includes(',') || str.includes('"') || str.includes('\n')) {
            return `"${str.replace(/"/g, '""')}"`
          }
          return str
        })
        .join(','),
    )
    .join('\n')

  const csv = `${header}\n${body}`
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
