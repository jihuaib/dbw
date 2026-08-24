import { confirmDialogService } from 'netnexus-ui';

/** confirm 的 Promise 包装：确认 true / 取消 false。 */
export function confirmAsync(options) {
    return new Promise(resolve => {
        confirmDialogService.confirm({
            ...options,
            onOk: () => resolve(true),
            onCancel: () => resolve(false)
        });
    });
}
