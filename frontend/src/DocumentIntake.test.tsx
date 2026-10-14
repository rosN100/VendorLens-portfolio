import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import DocumentIntake, { validateUpload } from './DocumentIntake';
describe('document intake', () => {
  it('shows four slots and disables completion until loaded', () => {
    const html=renderToStaticMarkup(<DocumentIntake caseId="case" actor="pilot_analyst" onCompleted={() => {}} />);
    for(const label of ['Security questionnaire','SOC 2 Type II report','Data processing agreement','Business continuity / DR plan']) expect(html).toContain(label);
    expect(html).toMatch(/disabled="">Complete intake/);
    expect(html).toContain('Partial uploads are saved');
  });
  it('checks file and size errors', () => {
    expect(validateUpload({name:'a.pdf',size:20*1024*1024})).toBeNull();
    expect(validateUpload({name:'a.pdf',size:20*1024*1024+1})).toContain('20 MiB');
    expect(validateUpload({name:'a.pdf',size:0})).toContain('empty');
    expect(validateUpload({name:'a.txt',size:100})).toContain('PDF');
  });
});
