import React from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell
} from 'recharts';

interface StatsChartProps {
  weeklyActivity: { name: string; downloads: number; shares: number }[];
  mediaDistribution: { name: string; value: number }[];
  topTags: { name: string; count: number }[];
}

const COLORS = ['#8b5cf6', '#ec4899', '#06b6d4'];
const TAG_BAR_COLOR = '#6366f1';

export const StatsChart: React.FC<StatsChartProps> = ({
  weeklyActivity,
  mediaDistribution,
  topTags,
}) => {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
      {/* Activity Chart */}
      <div className="bg-zinc-900 p-6 rounded-2xl border border-zinc-800 shadow-xl">
        <h3 className="text-lg font-semibold mb-4 text-zinc-100">Weekly Activity</h3>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={weeklyActivity}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
              <XAxis
                dataKey="name"
                stroke="#a1a1aa"
                tick={{fill: '#a1a1aa', fontSize: 12}}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                stroke="#a1a1aa"
                tick={{fill: '#a1a1aa', fontSize: 12}}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                contentStyle={{ backgroundColor: '#18181b', border: '1px solid #3f3f46', borderRadius: '8px', color: '#f4f4f5' }}
                itemStyle={{ color: '#e4e4e7' }}
                cursor={{fill: '#27272a'}}
              />
              <Legend wrapperStyle={{paddingTop: '10px'}}/>
              <Bar dataKey="downloads" fill="#8b5cf6" radius={[4, 4, 0, 0]} name="Downloads" />
              <Bar dataKey="shares" fill="#ec4899" radius={[4, 4, 0, 0]} name="Shares" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Media Type Distribution */}
      <div className="bg-zinc-900 p-6 rounded-2xl border border-zinc-800 shadow-xl">
        <h3 className="text-lg font-semibold mb-4 text-zinc-100">Media Distribution</h3>
        <div className="h-64 w-full flex justify-center items-center">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={mediaDistribution}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={80}
                fill="#8884d8"
                paddingAngle={5}
                dataKey="value"
              >
                {mediaDistribution.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                 contentStyle={{ backgroundColor: '#18181b', border: '1px solid #3f3f46', borderRadius: '8px', color: '#f4f4f5' }}
                 itemStyle={{ color: '#e4e4e7' }}
              />
              <Legend verticalAlign="bottom" height={36}/>
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Tags Distribution Chart */}
      <div className="bg-zinc-900 p-6 rounded-2xl border border-zinc-800 shadow-xl lg:col-span-2">
        <h3 className="text-lg font-semibold mb-4 text-zinc-100">Top Tags Overview</h3>
        <div className="h-72 w-full">
          {topTags.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={topTags}
                margin={{ top: 5, right: 30, left: 40, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#27272a" />
                <XAxis type="number" hide />
                <YAxis
                  dataKey="name"
                  type="category"
                  stroke="#a1a1aa"
                  tick={{fill: '#a1a1aa', fontSize: 12}}
                  width={80}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#18181b', border: '1px solid #3f3f46', borderRadius: '8px', color: '#f4f4f5' }}
                  cursor={{fill: '#27272a'}}
                />
                <Bar dataKey="count" fill={TAG_BAR_COLOR} radius={[0, 4, 4, 0]} barSize={20} name="Tagged Items">
                   {topTags.map((entry, index) => (
                      <Cell key={`cell-${index}`} fillOpacity={0.8 + (index % 2) * 0.2} />
                   ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex items-center justify-center text-zinc-500">
              No tags data available
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
