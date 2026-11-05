export type SettingsCategoryKey =
  | 'appearance'
  | 'integrations'
  | 'account'
  | 'project';

export type SettingsCategory = {
  key: SettingsCategoryKey;
  label: string;
  description: string;
};

export const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    key: 'appearance',
    label: '外观',
    description: '主题与界面显示',
  },
  {
    key: 'integrations',
    label: '服务',
    description: '模型接口与素材存储',
  },
  {
    key: 'account',
    label: '账号',
    description: '安全与成员管理',
  },
  {
    key: 'project',
    label: '项目',
    description: '协作、导入与导出',
  },
];

export function firstAvailableSettingsCategory(): SettingsCategoryKey {
  return 'appearance';
}
