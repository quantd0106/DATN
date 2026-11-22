'use client';
import {useSession} from '@/components/session-provider';
import {useVolatileDraft} from '@/components/volatile-draft';
import {draftStore} from '@/lib/draft-store';
import { useState, useTransition, useEffect } from 'react';
import type { RouteDefinition } from '@/config/routes';
import type { RouteParams, TeacherTutorInstructionConfig, TutorPolicyConfig, WorkspaceData } from '@/lib/domain';
import { adapter } from '@/lib/data';
import { ApiError, errorMessage } from '@/lib/api/errors';
import { StatePanel, Icon, useUnsavedChanges } from '@/components/ui';

type Props = {
  route: RouteDefinition;
  data: WorkspaceData | null;
  params: RouteParams;
  reload(): void;
};

export function TeacherTutorConfigView(props: Props) {
  const { data, reload } = props;
  const config = data?.kind === 'ready' ? data.teacherTutorConfig : undefined;
  if (config?.policy) {
    return <TutorPolicyForm key={`${config.classId}:${config.policy.version}`} config={config} initialPolicy={config.policy}
      className={data?.kind === 'ready' ? data.classSummary?.name : undefined} reload={reload} />;
  }
  return <TutorInstructionsForm {...props} />;
}

function TutorPolicyForm({ config, initialPolicy, className, reload }: {
  config: TeacherTutorInstructionConfig;
  initialPolicy: TutorPolicyConfig;
  className?: string;
  reload(): void;
}) {
  const [saved, setSaved] = useState(initialPolicy);
  const [policy, setPolicy] = useState(initialPolicy);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [pending, startTransition] = useTransition();
  const dirty = Object.keys(saved).some(key => saved[key as keyof TutorPolicyConfig] !== policy[key as keyof TutorPolicyConfig]);
  useUnsavedChanges(dirty);

  function change<K extends keyof TutorPolicyConfig>(field: K, value: TutorPolicyConfig[K]) {
    setPolicy(current => ({ ...current, [field]: value }));
    setNotice(null);
  }

  function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const update = adapter.tutor.updatePolicyConfig;
    if (!update || !dirty || pending || conflict) return;
    setError(null);
    setNotice(null);
    startTransition(async () => {
      try {
        const { version, ...values } = policy;
        const updated = await update(config.classId, { ...values, expectedVersion: version });
        if (!updated.policy) throw new ApiError('CONTRACT_MISMATCH', 'Máy chủ chưa trả về chính sách đã lưu.', 502, 'unknown');
        setSaved(updated.policy);
        setPolicy(updated.policy);
        setNotice('Đã lưu chính sách sư phạm của lớp.');
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setConflict(true);
          setError('Chính sách đã được cập nhật. Hãy tải lại cấu hình mới nhất trước khi lưu.');
        } else {
          setError(errorMessage(err));
        }
      }
    });
  }

  return <div className="teacher-tutor-config-container">
    <section className="card tutor-config-card">
      <h2 className="tutor-config-title">Chính sách sư phạm cho Trợ lý Lumi</h2>
      <p className="tutor-config-meta">{className && <><strong>{className}</strong> · </>}
        {saved.version === null ? 'Mặc định hệ thống' : `Phiên bản ${saved.version}`}
      </p>
      <div className="notice-box info"><Icon name="info" /><p>{config.disclaimer}</p></div>
    </section>
    <form className="card tutor-config-form" onSubmit={save}>
      {notice && <p className="notice success" role="status">{notice}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <fieldset className="tutor-policy-fields" disabled={pending || conflict}>
        <legend className="sr-only">Chính sách trợ lý của lớp</legend>
        <label className="tutor-policy-toggle">
          <input type="checkbox" checked={policy.allowGeneralKnowledge} onChange={e => change('allowGeneralKnowledge', e.target.checked)} />
          Cho phép kiến thức phổ thông chung
        </label>
        <label className="tutor-policy-toggle">
          <input type="checkbox" checked={policy.allowSolutionReveal} onChange={e => change('allowSolutionReveal', e.target.checked)} />
          Cho phép hiển thị lời giải cuối cùng
        </label>
        <label className="tutor-config-field">
          <span className="tutor-config-label">Mức độ gợi ý</span>
          <select className="input" value={policy.guidanceLevel} onChange={e => change('guidanceLevel', e.target.value as TutorPolicyConfig['guidanceLevel'])}>
            <option value="light">Nhẹ</option>
            <option value="balanced">Cân bằng</option>
            <option value="strong">Chi tiết</option>
          </select>
        </label>
        <label className="tutor-config-field">
          <span className="tutor-config-label">Số lượt trước khi mở gợi ý sâu</span>
          <input className="input" type="number" min={1} max={10} step={1} required
            value={Number.isNaN(policy.hintRevealAfter) ? '' : policy.hintRevealAfter} onChange={e => change('hintRevealAfter', e.target.valueAsNumber)} />
        </label>
        <label className="tutor-config-field">
          <span className="tutor-config-label">Độ dài câu trả lời tối đa (từ)</span>
          <input className="input" type="number" min={40} max={300} step={1} required
            value={Number.isNaN(policy.maxAnswerWords) ? '' : policy.maxAnswerWords} onChange={e => change('maxAnswerWords', e.target.valueAsNumber)} />
        </label>
      </fieldset>
      <div className="tutor-config-actions">
        {conflict
          ? <button type="button" className="button" onClick={reload}>Tải lại cấu hình</button>
          : <button type="button" className="button secondary" disabled={!dirty || pending} onClick={() => {
              setPolicy(saved);
              setError(null);
              setNotice(null);
            }}>Khôi phục bản đã lưu</button>}
        <button type="submit" className="button primary tutor-action-primary" disabled={!dirty || pending || conflict}>
          {pending ? 'Đang lưu…' : 'Lưu chính sách'}
        </button>
      </div>
    </form>
  </div>;
}

function TutorInstructionsForm({ data, params, reload }: Props) {
  const config = data && data.kind === 'ready' ? data.teacherTutorConfig : undefined;
  const initialText = config?.instructions ?? '';
  const {user}=useSession();
  const draftKey=`${user?.id}:tutor-config:${params.classId}`;
  const [instructions, setInstructions] = useVolatileDraft(draftKey, initialText);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Synchronize when config data loads
  useEffect(() => {
    if (config?.instructions !== undefined) {
      setInstructions(draftStore.get<string>(draftKey) ?? config.instructions);
    }
  }, [config?.instructions, draftKey]);

  const isDirty = instructions !== initialText;
  useUnsavedChanges(isDirty);

  if (!data) {
    return <StatePanel busy title="Đang tải cấu hình chỉ dẫn trợ lý…" />;
  }

  if (data.kind === 'unavailable') {
    return (
      <StatePanel title="Dịch vụ cấu hình trợ lý chưa sẵn sàng" message={data.message}>
        <button type="button" className="button" onClick={reload}>
          Thử lại
        </button>
      </StatePanel>
    );
  }

  if (data.kind === 'empty' || !config) {
    return <StatePanel title="Không tìm thấy cấu hình lớp học" message={data.kind === 'empty' ? data.message : 'Dữ liệu cấu hình không khả dụng.'} />;
  }

  const maxChars = config.maxChars || 2000;
  const classSummary = data.classSummary;

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (!params.classId) return;
    setSaveError(null);
    setSaveSuccess(false);

    startTransition(async () => {
      try {
        await adapter.tutor.updateConfig(params.classId, instructions.trim());
        setSaveSuccess(true);
        reload();
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : 'Có lỗi khi lưu cấu hình chỉ dẫn.');
      }
    });
  };

  const handleReset = () => {
    setInstructions(initialText);
    setSaveError(null);
    setSaveSuccess(false);
  };

  return (
    <div className="teacher-tutor-config-container">
      {/* Top Description and Class Info */}
      <div className="card tutor-config-card">
        <div className="tutor-config-header">
          <div>
            <h2 className="tutor-config-title">Chỉ dẫn sư phạm cho Trợ lý Lumi</h2>
            <p className="tutor-config-meta">
              Lớp: <strong>{classSummary?.name ?? params.classId}</strong> · Cập nhật gần nhất: {config.updatedAt}
            </p>
          </div>
          <span className="tutor-config-badge">Cấu hình lớp</span>
        </div>

        {/* Student Privacy Notice under Rules M2, C5, C6 */}
        <div className="notice-box info">
          <Icon name="info" />
          <p>
            <strong>Bảo vệ quyền riêng tư học sinh:</strong> {config.disclaimer}
          </p>
        </div>
      </div>

      {/* Main Config Form */}
      <form onSubmit={handleSave} className="card tutor-config-form">
        {saveSuccess && (
          <div className="notice success" role="status">
            Đã lưu cấu hình chỉ dẫn sư phạm thành công. Lumi sẽ áp dụng định hướng này cho các phản hồi trong lớp.
          </div>
        )}

        {saveError && (
          <div className="notice error" role="alert">
            {saveError}
          </div>
        )}

        <div className="tutor-config-field">
          <label htmlFor="tutorInstructionsArea" className="tutor-config-label">
            Nội dung chỉ dẫn phương pháp sư phạm:
          </label>
          <p className="tutor-config-hint">
            Gợi ý: Hướng dẫn Lumi ưu tiên cách tiếp cận từng bước, khuyến khích học sinh tự tìm tòi công thức, hoặc tập trung vào các dạng bài tập trọng tâm trong học kỳ.
          </p>
          <textarea
            id="tutorInstructionsArea"
            className="tutor-config-textarea"
            rows={8}
            maxLength={maxChars}
            value={instructions}
            onChange={e => {
              setInstructions(e.target.value);
              setSaveSuccess(false);
            }}
            placeholder="ví dụ: Khuyến khích học sinh tự tính toán tọa độ đỉnh và tự lập bảng biến thiên. Không giải tắt hoặc đưa ngay đáp án cuối cùng…"
            disabled={isPending}
          />
          <div className="tutor-config-counter-row">
            <span>{isDirty ? '• Bạn có thay đổi chưa lưu' : 'Chưa có thay đổi mới'}</span>
            <span className={`tutor-config-char-count ${instructions.length > maxChars * 0.9 ? 'is-near-limit' : ''}`}>
              {instructions.length} / {maxChars} ký tự
            </span>
          </div>
        </div>

        <div className="tutor-config-actions">
          <button
            type="button"
            className="button secondary"
            onClick={handleReset}
            disabled={isPending || !isDirty}
          >
            Khôi phục ban đầu
          </button>
          <button
            type="submit"
            className="button primary tutor-action-primary"
            disabled={isPending || !isDirty}
          >
            {isPending ? 'Đang lưu…' : 'Lưu chỉ dẫn'}
          </button>
        </div>
      </form>
    </div>
  );
}
