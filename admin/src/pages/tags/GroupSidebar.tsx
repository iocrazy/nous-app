import { useState } from 'react'
import {
  Input,
  Menu,
  Modal,
  Space,
  Typography,
} from '@arco-design/web-react'
import {
  IconPlus,
  IconTag,
  IconApps,
  IconFolder,
} from '@arco-design/web-react/icon'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { TagGroup } from '../../api/endpoints/tags'

interface GroupSidebarProps {
  groups: TagGroup[]
  totalTags: number
  uncategorizedCount: number
  selectedGroup: string | null // null = "all", "uncategorized", or group ID
  onSelect: (groupId: string | null) => void
  onCreateGroup: (name: string) => void
  onRenameGroup: (id: string, name: string) => void
  onDeleteGroup: (id: string) => void
  onReorderGroups: (ids: string[]) => void
}

function SortableGroupItem({
  group,
  onSelect,
  onRename,
  onDelete,
}: {
  group: TagGroup
  onSelect: () => void
  onRename: (name: string) => void
  onDelete: () => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: group.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      onContextMenu={(e) => {
        e.preventDefault()
        Modal.confirm({
          title: 'Group Actions',
          content: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  Modal.destroyAll()
                  const newName = prompt('Rename group:', group.name)
                  if (newName && newName !== group.name) onRename(newName)
                }}
              >
                Rename
              </Typography.Text>
              <Typography.Text
                type="error"
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  Modal.destroyAll()
                  Modal.confirm({
                    title: 'Delete Group',
                    content: `Delete "${group.name}"? Tags will become uncategorized.`,
                    okButtonProps: { status: 'danger' },
                    onOk: onDelete,
                  })
                }}
              >
                Delete
              </Typography.Text>
            </Space>
          ),
          footer: null,
        })
      }}
    >
      <Menu.Item key={group.id} onClick={onSelect}>
        <span {...attributes} {...listeners} style={{ cursor: 'grab', marginRight: 6 }}>
          ⠿
        </span>
        <IconFolder style={{ marginRight: 6 }} />
        {group.name}
        <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
          {group.tag_count}
        </span>
      </Menu.Item>
    </div>
  )
}

export function GroupSidebar({
  groups,
  totalTags,
  uncategorizedCount,
  selectedGroup,
  onSelect,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onReorderGroups,
}: GroupSidebarProps) {
  const [isCreating, setIsCreating] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = groups.findIndex((g) => g.id === active.id)
    const newIndex = groups.findIndex((g) => g.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const reordered = [...groups]
    const [moved] = reordered.splice(oldIndex, 1)
    reordered.splice(newIndex, 0, moved)
    onReorderGroups(reordered.map((g) => g.id))
  }

  const handleCreateSubmit = () => {
    const name = newGroupName.trim()
    if (!name) return
    onCreateGroup(name)
    setNewGroupName('')
    setIsCreating(false)
  }

  return (
    <div style={{ width: 220, borderRight: '1px solid var(--color-border)', height: '100%', overflow: 'auto' }}>
      <Menu
        selectedKeys={selectedGroup === null ? ['all'] : [selectedGroup]}
        style={{ background: 'transparent' }}
      >
        <Menu.Item key="all" onClick={() => onSelect(null)}>
          <IconApps style={{ marginRight: 6 }} />
          All
          <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
            {totalTags}
          </span>
        </Menu.Item>
        <Menu.Item key="uncategorized" onClick={() => onSelect('uncategorized')}>
          <IconTag style={{ marginRight: 6 }} />
          Uncategorized
          <span style={{ float: 'right', color: 'var(--color-text-3)', fontSize: 12 }}>
            {uncategorizedCount}
          </span>
        </Menu.Item>
      </Menu>

      <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Groups ({groups.length})
        </Typography.Text>
        <IconPlus
          style={{ cursor: 'pointer', fontSize: 14 }}
          onClick={() => setIsCreating(true)}
        />
      </div>

      {isCreating && (
        <div style={{ padding: '0 12px 8px' }}>
          <Input
            size="small"
            autoFocus
            placeholder="Group name"
            value={newGroupName}
            onChange={setNewGroupName}
            onPressEnter={handleCreateSubmit}
            onBlur={() => {
              if (!newGroupName.trim()) setIsCreating(false)
            }}
          />
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={groups.map((g) => g.id)} strategy={verticalListSortingStrategy}>
          <Menu selectedKeys={selectedGroup && selectedGroup !== 'uncategorized' ? [selectedGroup] : []}>
            {groups.map((group) => (
              <SortableGroupItem
                key={group.id}
                group={group}
                onSelect={() => onSelect(group.id)}
                onRename={(name) => onRenameGroup(group.id, name)}
                onDelete={() => onDeleteGroup(group.id)}
              />
            ))}
          </Menu>
        </SortableContext>
      </DndContext>
    </div>
  )
}
