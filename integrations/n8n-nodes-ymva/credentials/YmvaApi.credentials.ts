import type {
  IAuthenticateGeneric,
  ICredentialTestRequest,
  ICredentialType,
  INodeProperties,
} from 'n8n-workflow';

export class YmvaApi implements ICredentialType {
  name = 'ymvaApi';
  displayName = 'YMVA API';
  documentationUrl = 'https://github.com/';
  properties: INodeProperties[] = [
    {
      displayName: 'YMVA Address',
      name: 'baseUrl',
      type: 'string',
      default: '',
      placeholder: 'https://voice.yellowmetal.co',
      description: 'The public address of your YMVA dashboard, without /api',
    },
    {
      displayName: 'API Key',
      name: 'apiKey',
      type: 'string',
      typeOptions: { password: true },
      default: '',
      description: 'Create one in YMVA: Settings → API keys',
    },
  ];

  authenticate: IAuthenticateGeneric = {
    type: 'generic',
    properties: {
      headers: { Authorization: '=Bearer {{$credentials.apiKey}}' },
    },
  };

  test: ICredentialTestRequest = {
    request: {
      baseURL: '={{$credentials.baseUrl.replace(/\\/+$/, "")}}',
      url: '/api/auth/me',
    },
  };
}
