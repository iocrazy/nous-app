import { useState, type ReactNode } from 'react'
import { Button, Input, Popover, Divider } from '@arco-design/web-react'
import {
  IconFilter,
  IconSort,
  IconSettings,
  IconRefresh,
} from '@arco-design/web-react/icon'
import type { NotionTableToolbarProps, NotionColumnDef } from './types'
import { FilterChip } from './FilterChip'
import { FilterEditor } from './FilterEditor'
import { SortChip, SortEditorContent } from './SortEditor'
import { ColumnVisibility } from './ColumnVisibility'

interface ToolbarProps<T> extends NotionTableToolbarProps<T> {
  extra?: ReactNode
}

export function NotionTableToolbar<T>({
  columns,
  filters,
  sorts,
  visibleColumns,
  search,
  onAddFilter,
  onRemoveFilter,
  onUpdateFilter,
  onAddSort,
  onRemoveSort,
  onUpdateSort,
  onToggleColumn,
  onResetColumns,
  onSetSearch,
  onResetAll,
  extra,
}: ToolbarProps<T>) {
  const [addFilterOpen, setAddFilterOpen] = useState(false)
  const [addSortOpen, setAddSortOpen] = useState(false)
  const [colVisOpen, setColVisOpen] = useState(false)

  const hasFiltersOrSorts = filters.length > 0 || sorts.length > 0

  const unknownColumns = columns as unknown as NotionColumnDef<unknown>[]

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Input.Search
          placeholder="Search..."
          value={search}
          onChange={onSetSearch}
          allowClear
          size="small"
          style={{ width: 240 }}
        />

        <Divider type="vertical" style={{ margin: '0 4px' }} />

        <Popover
          trigger="click"
          popupVisible={addFilterOpen}
          onVisibleChange={setAddFilterOpen}
          content={
            <FilterEditor
              columns={unknownColumns}
              onSubmit={(f) => {
                onAddFilter(f)
                setAddFilterOpen(false)
              }}
              onCancel={() => setAddFilterOpen(false)}
            />
          }
        >
          <Button size="mini" type="secondary" icon={<IconFilter />}>
            Filter
          </Button>
        </Popover>

        <Popover
          trigger="click"
          popupVisible={addSortOpen}
          onVisibleChange={setAddSortOpen}
          content={
            <SortEditorContent
              columns={unknownColumns}
              existingSortFields={sorts.map((s) => s.field)}
              onSubmit={(s) => {
                onAddSort(s)
                setAddSortOpen(false)
              }}
              onCancel={() => setAddSortOpen(false)}
            />
          }
        >
          <Button size="mini" type="secondary" icon={<IconSort />}>
            Sort
          </Button>
        </Popover>

        <Popover
          trigger="click"
          popupVisible={colVisOpen}
          onVisibleChange={setColVisOpen}
          content={
            <ColumnVisibility
              columns={unknownColumns}
              visibleColumns={visibleColumns}
              onToggle={onToggleColumn}
              onReset={onResetColumns}
            />
          }
        >
          <Button size="mini" type="secondary" icon={<IconSettings />}>
            Properties
          </Button>
        </Popover>

        {hasFiltersOrSorts && (
          <Button size="mini" type="text" icon={<IconRefresh />} onClick={onResetAll}>
            Reset
          </Button>
        )}

        {extra && (
          <>
            <div style={{ flex: 1 }} />
            {extra}
          </>
        )}
      </div>

      {hasFiltersOrSorts && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
          {filters.map((f, i) => (
            <FilterChip
              key={`f-${i}`}
              filter={f}
              index={i}
              columns={unknownColumns}
              onUpdate={onUpdateFilter}
              onRemove={onRemoveFilter}
            />
          ))}
          {sorts.map((s, i) => (
            <SortChip
              key={`s-${i}`}
              sort={s}
              index={i}
              columns={unknownColumns}
              onRemove={onRemoveSort}
              onUpdate={onUpdateSort}
            />
          ))}
        </div>
      )}
    </div>
  )
}
