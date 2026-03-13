import { useMemo, useState } from 'react'
import {
  Button,
  Dropdown,
  Menu,
  Message,
  Modal,
  Select,
  Space,
  Tag,
} from '@arco-design/web-react'
import {
  IconPlus,
  IconMore,
  IconDelete,
  IconEdit,
} from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import {
  useTagGroups,
  useCreateGroup,
  useUpdateGroup,
  useDeleteGroup,
  useReorderGroups,
  useCreateTag,
  useUpdateTag,
  useDeleteTag,
  useBatchTagAction,
  type TagData,
} from '../../api/endpoints/tags'
import { GroupSidebar } from './GroupSidebar'
import { TagFormModal } from './TagFormModal'

export function TagsPage() {
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingTag, setEditingTag] = useState<TagData | null>(null)
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([])

  // Groups
  const { data: groupsData } = useTagGroups()
  const createGroup = useCreateGroup()
  const updateGroup = useUpdateGroup()
  const deleteGroup = useDeleteGroup()
  const reorderGroups = useReorderGroups()

  // Tags
  const createTag = useCreateTag()
  const updateTag = useUpdateTag()
  const deleteTagMut = useDeleteTag()
  const batchAction = useBatchTagAction()

  const groups = groupsData?.groups ?? []
  const totalTags = groupsData?.total_tags ?? 0
  const uncategorizedCount = groupsData?.uncategorized_count ?? 0

  // --- Handlers ---

  const handleCreateTag = (values: Parameters<typeof createTag.mutate>[0]) => {
    createTag.mutate(values, {
      onSuccess: () => {
        Message.success('Tag created')
        setModalVisible(false)
      },
    })
  }

  const handleUpdateTag = (values: Parameters<typeof createTag.mutate>[0]) => {
    if (!editingTag) return
    updateTag.mutate(
      { id: editingTag.id, values },
      {
        onSuccess: () => {
          Message.success('Tag updated')
          setModalVisible(false)
          setEditingTag(null)
        },
      },
    )
  }

  const handleDeleteTag = (tag: TagData) => {
    Modal.confirm({
      title: 'Delete Tag',
      content: `Delete "${tag.name}"? This will remove it from all resources.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteTagMut.mutateAsync(tag.id, {
          onSuccess: () => Message.success('Tag deleted'),
        }),
    })
  }

  const handleBatchMove = (groupId: string | undefined) => {
    batchAction.mutate(
      { action: 'move', tag_ids: selectedTagIds, group_id: groupId },
      {
        onSuccess: () => {
          Message.success(`Moved ${selectedTagIds.length} tags`)
          setSelectedTagIds([])
        },
      },
    )
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: 'Delete Tags',
      content: `Delete ${selectedTagIds.length} tags? This cannot be undone.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        batchAction.mutateAsync(
          { action: 'delete', tag_ids: selectedTagIds },
          {
            onSuccess: () => {
              Message.success(`Deleted ${selectedTagIds.length} tags`)
              setSelectedTagIds([])
            },
          },
        ),
    })
  }

  // --- Column definitions ---

  const columns = useMemo<NotionColumnDef<TagData>[]>(
    () => [
      {
        key: 'name',
        header: 'Name',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 160,
        cell: (row) => (
          <Space>
            <span
              style={{
                display: 'inline-block',
                width: 14,
                height: 14,
                borderRadius: 4,
                background: row.color || '#6366f1',
              }}
            />
            <span>{row.icon ? `${row.icon} ` : ''}{row.name}</span>
            {row.name_zh && (
              <span style={{ color: 'var(--color-text-3)', fontSize: 12 }}>
                ({row.name_zh})
              </span>
            )}
          </Space>
        ),
      },
      {
        key: 'type',
        header: 'Type',
        type: 'select',
        filterable: true,
        size: 100,
        filterOptions: [
          { label: 'System', value: 'system' },
          { label: 'User', value: 'user' },
          { label: 'Time', value: 'time' },
        ],
        cell: (row) => (
          <Tag size="small" color={row.type === 'system' ? 'blue' : row.type === 'time' ? 'green' : 'gray'}>
            {row.type}
          </Tag>
        ),
      },
      {
        key: 'group_name',
        header: 'Group',
        type: 'text',
        size: 130,
        cell: (row) => row.group_name || '-',
      },
      {
        key: 'usage_count',
        header: 'Used',
        type: 'number',
        sortable: true,
        size: 80,
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        sortable: true,
        size: 130,
        cell: (row) => new Date(row.created_at).toLocaleDateString(),
      },
      {
        key: 'actions',
        header: '',
        type: 'text',
        required: true,
        size: 60,
        cell: (row) => (
          <Dropdown
            droplist={
              <Menu>
                <Menu.Item
                  key="edit"
                  onClick={() => {
                    setEditingTag(row)
                    setModalVisible(true)
                  }}
                >
                  <Space><IconEdit /> Edit</Space>
                </Menu.Item>
                <Menu.Item
                  key="delete"
                  onClick={() => handleDeleteTag(row)}
                  style={{ color: 'rgb(var(--danger-6))' }}
                >
                  <Space><IconDelete /> Delete</Space>
                </Menu.Item>
              </Menu>
            }
            position="br"
          >
            <IconMore style={{ cursor: 'pointer', fontSize: 18 }} />
          </Dropdown>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  // --- NotionTable setup ---

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<TagData>({
    tableKey: `admin-tags${selectedGroup ? `-${selectedGroup}` : ''}`,
    columns,
    defaultSorts: [{ field: 'sort_order', direction: 'asc' }],
    fetchData: async ({ page, pageSize, sorts, search }) => {
      const sortBy = sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/tags', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(selectedGroup && { group_id: selectedGroup }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  // --- Batch bar ---

  const batchBar = selectedTagIds.length > 0 && (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 16px',
        background: 'var(--color-fill-2)',
        borderRadius: 8,
        marginBottom: 8,
      }}
    >
      <span>{selectedTagIds.length} selected</span>
      <Select
        size="small"
        placeholder="Move to..."
        style={{ width: 150 }}
        allowClear
        onChange={(val) => handleBatchMove(val || undefined)}
      >
        <Select.Option value="">Uncategorized</Select.Option>
        {groups.map((g) => (
          <Select.Option key={g.id} value={g.id}>
            {g.name}
          </Select.Option>
        ))}
      </Select>
      <Button
        size="small"
        status="danger"
        icon={<IconDelete />}
        onClick={handleBatchDelete}
      >
        Delete
      </Button>
      <Button
        size="small"
        type="text"
        onClick={() => setSelectedTagIds([])}
      >
        Clear
      </Button>
    </div>
  )

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 60px)' }}>
      <GroupSidebar
        groups={groups}
        totalTags={totalTags}
        uncategorizedCount={uncategorizedCount}
        selectedGroup={selectedGroup}
        onSelect={setSelectedGroup}
        onCreateGroup={(name) =>
          createGroup.mutate(name, {
            onSuccess: () => Message.success('Group created'),
          })
        }
        onRenameGroup={(id, name) =>
          updateGroup.mutate(
            { id, values: { name } },
            { onSuccess: () => Message.success('Group renamed') },
          )
        }
        onDeleteGroup={(id) =>
          deleteGroup.mutate(id, {
            onSuccess: () => {
              Message.success('Group deleted')
              if (selectedGroup === id) setSelectedGroup(null)
            },
          })
        }
        onReorderGroups={(ids) => reorderGroups.mutate(ids)}
      />

      <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
        {batchBar}
        <NotionTable<TagData>
          table={table}
          toolbarProps={toolbarProps}
          pagination={pagination}
          onPageChange={setPage}
          isLoading={isLoading}
          title="Tags"
          emptyText="No tags found"
          scrollX={700}
          toolbarExtra={
            <Button
              type="primary"
              icon={<IconPlus />}
              size="small"
              onClick={() => {
                setEditingTag(null)
                setModalVisible(true)
              }}
            >
              New Tag
            </Button>
          }
        />
      </div>

      <TagFormModal
        visible={modalVisible}
        tag={editingTag}
        groups={groups}
        onSubmit={editingTag ? handleUpdateTag : handleCreateTag}
        onClose={() => {
          setModalVisible(false)
          setEditingTag(null)
        }}
      />
    </div>
  )
}
