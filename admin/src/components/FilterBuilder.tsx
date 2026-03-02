import { Select, Input, InputNumber, Button, Space, Tag } from '@arco-design/web-react'
import { IconPlus, IconClose } from '@arco-design/web-react/icon'

// ============================================
// Types
// ============================================

export interface FilterFieldOption {
  value: string
  label: string
}

export interface FilterField {
  key: string
  label: string
  type: 'text' | 'number' | 'select'
  options?: FilterFieldOption[]
}

const TEXT_OPERATORS = [
  { value: 'contains', label: 'contains' },
  { value: 'equals', label: 'equals' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'not_contains', label: 'does not contain' },
]

const NUMBER_OPERATORS = [
  { value: 'eq', label: '=' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '>=' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '<=' },
]

const SELECT_OPERATORS = [
  { value: 'is', label: 'is' },
  { value: 'is_not', label: 'is not' },
]

export interface FilterCondition {
  id: string
  field: string
  operator: string
  value: string
}

function getOperators(field: FilterField | undefined) {
  if (!field) return TEXT_OPERATORS
  switch (field.type) {
    case 'number':
      return NUMBER_OPERATORS
    case 'select':
      return SELECT_OPERATORS
    default:
      return TEXT_OPERATORS
  }
}

function getDefaultOperator(field: FilterField | undefined): string {
  if (!field) return 'contains'
  switch (field.type) {
    case 'number':
      return 'gt'
    case 'select':
      return 'is'
    default:
      return 'contains'
  }
}

let nextId = 1
function genId(): string {
  return `f_${nextId++}`
}

// ============================================
// Component
// ============================================

interface FilterBuilderProps {
  fields: FilterField[]
  filters: FilterCondition[]
  onChange: (filters: FilterCondition[]) => void
  maxFilters?: number
}

export function FilterBuilder({
  fields,
  filters,
  onChange,
  maxFilters = 6,
}: FilterBuilderProps) {
  const addFilter = () => {
    if (filters.length >= maxFilters) return
    const firstField = fields[0]
    onChange([
      ...filters,
      {
        id: genId(),
        field: firstField?.key ?? '',
        operator: getDefaultOperator(firstField),
        value: '',
      },
    ])
  }

  const updateFilter = (id: string, patch: Partial<FilterCondition>) => {
    onChange(
      filters.map((f) => {
        if (f.id !== id) return f
        const updated = { ...f, ...patch }
        // Reset operator and value when field changes
        if (patch.field && patch.field !== f.field) {
          const newField = fields.find((fd) => fd.key === patch.field)
          updated.operator = getDefaultOperator(newField)
          updated.value = ''
        }
        return updated
      }),
    )
  }

  const removeFilter = (id: string) => {
    onChange(filters.filter((f) => f.id !== id))
  }

  return (
    <div>
      {filters.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            marginBottom: 12,
            padding: 12,
            background: 'var(--color-fill-1)',
            borderRadius: 8,
            border: '1px solid var(--color-border)',
          }}
        >
          {filters.map((filter, index) => {
            const fieldDef = fields.find((f) => f.key === filter.field)
            const operators = getOperators(fieldDef)

            return (
              <div
                key={filter.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                {/* Where / And label */}
                <Tag
                  size="small"
                  color="arcoblue"
                  style={{
                    width: 52,
                    textAlign: 'center',
                    flexShrink: 0,
                    fontFamily: 'monospace',
                    fontSize: 12,
                  }}
                >
                  {index === 0 ? 'Where' : 'And'}
                </Tag>

                {/* Field selector */}
                <Select
                  value={filter.field}
                  onChange={(val) => updateFilter(filter.id, { field: val })}
                  style={{ width: 160 }}
                  size="small"
                >
                  {fields.map((f) => (
                    <Select.Option key={f.key} value={f.key}>
                      {f.label}
                    </Select.Option>
                  ))}
                </Select>

                {/* Operator selector */}
                <Select
                  value={filter.operator}
                  onChange={(val) => updateFilter(filter.id, { operator: val })}
                  style={{ width: 140 }}
                  size="small"
                >
                  {operators.map((o) => (
                    <Select.Option key={o.value} value={o.value}>
                      {o.label}
                    </Select.Option>
                  ))}
                </Select>

                {/* Value input — varies by field type */}
                {fieldDef?.type === 'select' ? (
                  <Select
                    value={filter.value}
                    onChange={(val) => updateFilter(filter.id, { value: val })}
                    style={{ width: 180 }}
                    size="small"
                    placeholder="Select..."
                    allowClear
                    onClear={() => updateFilter(filter.id, { value: '' })}
                  >
                    {(fieldDef.options ?? []).map((o) => (
                      <Select.Option key={o.value} value={o.value}>
                        {o.label}
                      </Select.Option>
                    ))}
                  </Select>
                ) : fieldDef?.type === 'number' ? (
                  <InputNumber
                    value={filter.value ? Number(filter.value) : undefined}
                    onChange={(val) =>
                      updateFilter(filter.id, { value: val != null ? String(val) : '' })
                    }
                    style={{ width: 180 }}
                    size="small"
                    placeholder="Value..."
                  />
                ) : (
                  <Input
                    value={filter.value}
                    onChange={(val) => updateFilter(filter.id, { value: val })}
                    style={{ width: 180 }}
                    size="small"
                    placeholder="Value..."
                    allowClear
                  />
                )}

                {/* Remove button */}
                <Button
                  type="text"
                  size="mini"
                  icon={<IconClose />}
                  onClick={() => removeFilter(filter.id)}
                  style={{ color: 'var(--color-text-3)', flexShrink: 0 }}
                />
              </div>
            )
          })}
        </div>
      )}

      <Space size={8}>
        <Button
          type="text"
          size="small"
          icon={<IconPlus />}
          onClick={addFilter}
          disabled={filters.length >= maxFilters}
        >
          Add filter
        </Button>
        {filters.length > 0 && (
          <Button
            type="text"
            size="small"
            onClick={() => onChange([])}
            style={{ color: 'var(--color-text-3)' }}
          >
            Clear all
          </Button>
        )}
      </Space>
    </div>
  )
}

// ============================================
// Helper: extract filter value by field key
// ============================================

export function getFilterValue(
  filters: FilterCondition[],
  fieldKey: string,
): string | undefined {
  const f = filters.find((c) => c.field === fieldKey && c.value)
  return f?.value || undefined
}
