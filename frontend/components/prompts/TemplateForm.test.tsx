import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? _k) }) }));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { TemplateForm, type TemplateFormValue } from './TemplateForm';

const value: TemplateFormValue = { title: 'T', group: '', positive: 'p', negative: '', exampleIds: ['1'] };

describe('TemplateForm', () => {
  it('reports edits through onChange without owning state', () => {
    const onChange = vi.fn();
    render(<TemplateForm value={value} onChange={onChange} examples={[{ id: '1', url: '/api/v1/resources/1/cover' }, { id: '2', url: null }]} groups={['Lighting']} />);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...value, title: 'New' });
    fireEvent.click(screen.getByRole('button', { name: 'Lighting' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, group: 'Lighting' });
  });
  it('ticks and unticks examples', () => {
    const onChange = vi.fn();
    render(<TemplateForm value={value} onChange={onChange} examples={[{ id: '1', url: null }, { id: '2', url: null }]} groups={[]} />);
    fireEvent.click(screen.getByTestId('template-example-2'));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, exampleIds: ['1', '2'] });
    fireEvent.click(screen.getByTestId('template-example-1'));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, exampleIds: [] });
  });
});
