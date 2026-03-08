import { Tabs } from '@arco-design/web-react'
import { OverviewTab } from './OverviewTab'
import { TransactionsTab } from './TransactionsTab'
import { OrdersTab } from './OrdersTab'
import { PackagesTab } from './PackagesTab'
import { PricingTab } from './PricingTab'

const { TabPane } = Tabs

export function CreditsPage() {
  return (
    <div style={{ padding: '0 4px' }}>
      <Tabs defaultActiveTab="overview" size="large" type="rounded">
        <TabPane key="overview" title="Overview">
          <OverviewTab />
        </TabPane>
        <TabPane key="transactions" title="Transactions">
          <TransactionsTab />
        </TabPane>
        <TabPane key="orders" title="Orders">
          <OrdersTab />
        </TabPane>
        <TabPane key="packages" title="Packages">
          <PackagesTab />
        </TabPane>
        <TabPane key="pricing" title="Pricing">
          <PricingTab />
        </TabPane>
      </Tabs>
    </div>
  )
}
