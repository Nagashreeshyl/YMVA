import type {
  IDataObject,
  IExecuteFunctions,
  IHttpRequestMethods,
  ILoadOptionsFunctions,
  INodeExecutionData,
  INodePropertyOptions,
  INodeType,
  INodeTypeDescription,
  ResourceMapperFields,
} from 'n8n-workflow';
import { NodeApiError, NodeOperationError } from 'n8n-workflow';

type Ctx = IExecuteFunctions | ILoadOptionsFunctions;

const humanize = (name: string) => {
  const s = name.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
};

async function ymva(
  ctx: Ctx,
  method: IHttpRequestMethods,
  path: string,
  body?: IDataObject,
): Promise<any> {
  const { baseUrl } = await ctx.getCredentials('ymvaApi');
  const response = await ctx.helpers.httpRequestWithAuthentication.call(ctx, 'ymvaApi', {
    method,
    url: `${String(baseUrl).replace(/\/+$/, '')}/api${path}`,
    body,
    json: true,
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
  });
  if (response.statusCode >= 400) {
    // YMVA's own explanation (e.g. "Choose the number to call from"), not n8n's generic one.
    const detail = response.body?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: IDataObject) => d.msg).join('; ')
          : `YMVA answered ${response.statusCode}`;
    throw new NodeApiError(ctx.getNode(), response.body ?? {}, {
      message,
      httpCode: String(response.statusCode),
    });
  }
  return response.body;
}

export class Ymva implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'YMVA',
    name: 'ymva',
    icon: 'file:ymva.svg',
    group: ['output'],
    version: 1,
    subtitle: '={{$parameter["operation"]}}',
    description: 'Start AI phone calls with a YMVA agent and get their results',
    defaults: { name: 'YMVA' },
    inputs: ['main'],
    outputs: ['main'],
    credentials: [{ name: 'ymvaApi', required: true }],
    properties: [
      {
        displayName: 'Operation',
        name: 'operation',
        type: 'options',
        noDataExpression: true,
        options: [
          {
            name: 'Start Call',
            value: 'startCall',
            action: 'Start a phone call with an agent',
          },
          {
            name: 'Get Call',
            value: 'getCall',
            action: 'Get a call status and result',
          },
          {
            name: 'List Agents',
            value: 'listAgents',
            action: 'List the agents',
          },
        ],
        default: 'startCall',
      },
      {
        displayName: 'Agent Name or ID',
        name: 'agentId',
        type: 'options',
        typeOptions: { loadOptionsMethod: 'getAgents' },
        required: true,
        default: '',
        description:
          'The agent that makes the call. Choose from the list, or specify an ID using an <a href="https://docs.n8n.io/code/expressions/">expression</a>.',
        displayOptions: { show: { operation: ['startCall'] } },
      },
      {
        displayName: 'Phone Number to Call',
        name: 'to',
        type: 'string',
        required: true,
        default: '',
        placeholder: '+919845011111',
        description: 'With country code; 10-digit Indian numbers also work',
        displayOptions: { show: { operation: ['startCall'] } },
      },
      {
        displayName: 'Customer Details',
        name: 'details',
        type: 'resourceMapper',
        noDataExpression: true,
        default: { mappingMode: 'defineBelow', value: null },
        required: true,
        description:
          "The details this agent needs for the call (its {{variables}}), e.g. the customer's name",
        displayOptions: { show: { operation: ['startCall'] } },
        typeOptions: {
          loadOptionsDependsOn: ['agentId'],
          resourceMapper: {
            resourceMapperMethod: 'getAgentDetails',
            mode: 'add',
            valuesLabel: 'Customer Details',
            fieldWords: { singular: 'detail', plural: 'details' },
            addAllFields: true,
            multiKeyMatch: false,
            supportAutoMap: false,
            noFieldsError: 'This agent needs no customer details',
            refreshStaleSchemaOnOpen: true,
          },
        },
      },
      {
        displayName: 'Additional Fields',
        name: 'additional',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: { show: { operation: ['startCall'] } },
        options: [
          {
            displayName: 'State',
            name: 'state',
            type: 'string',
            default: '',
            placeholder: 'Karnataka',
            description:
              "The customer's state; the call opens in its language (Karnataka → Kannada, TN → Tamil, UP → Hindi). Names or short codes. The agent must have that language turned on, otherwise it uses its default.",
          },
          {
            displayName: 'Language',
            name: 'language',
            type: 'options',
            default: '',
            description: 'Open the call in this language. Wins over State.',
            options: [
              { name: '(From State or Agent Default)', value: '' },
              { name: 'Bengali', value: 'bn' },
              { name: 'English', value: 'en' },
              { name: 'Gujarati', value: 'gu' },
              { name: 'Hindi', value: 'hi' },
              { name: 'Kannada', value: 'kn' },
              { name: 'Malayalam', value: 'ml' },
              { name: 'Marathi', value: 'mr' },
              { name: 'Odia', value: 'od' },
              { name: 'Punjabi', value: 'pa' },
              { name: 'Tamil', value: 'ta' },
              { name: 'Telugu', value: 'te' },
            ],
          },
          {
            displayName: 'Call From Number Name or ID',
            name: 'fromNumberId',
            type: 'options',
            typeOptions: { loadOptionsMethod: 'getNumbers' },
            default: '',
            description:
              'Leave empty to use the number set for this agent (or the only number). Choose from the list, or specify an ID using an <a href="https://docs.n8n.io/code/expressions/">expression</a>.',
          },
          {
            displayName: 'Callback URL',
            name: 'callbackUrl',
            type: 'string',
            default: '',
            placeholder: '={{ $execution.resumeUrl }}',
            description:
              'YMVA POSTs the result here when the call ends (outcome, summary, details). Use {{ $execution.resumeUrl }} with a Wait node set to "On Webhook Call".',
          },
        ],
      },
      {
        displayName: 'Call ID',
        name: 'callId',
        type: 'string',
        required: true,
        default: '',
        displayOptions: { show: { operation: ['getCall'] } },
      },
    ],
  };

  methods = {
    loadOptions: {
      async getAgents(this: ILoadOptionsFunctions): Promise<INodePropertyOptions[]> {
        const agents = (await ymva(this, 'GET', '/agents')) as IDataObject[];
        return agents
          .filter((a) => a.status !== 'archived')
          .map((a) => ({
            name: `${a.name} (${a.status})`,
            value: a.id as string,
          }));
      },
      async getNumbers(this: ILoadOptionsFunctions): Promise<INodePropertyOptions[]> {
        const numbers = (await ymva(this, 'GET', '/numbers')) as IDataObject[];
        return numbers
          .filter((n) => n.is_active)
          .map((n) => ({
            name: n.label ? `${n.e164} · ${n.label}` : (n.e164 as string),
            value: n.id as string,
          }));
      },
    },
    resourceMapping: {
      async getAgentDetails(this: ILoadOptionsFunctions): Promise<ResourceMapperFields> {
        const agentId = this.getCurrentNodeParameter('agentId');
        const agents = (await ymva(this, 'GET', '/agents')) as IDataObject[];
        const agent = agents.find((a) => a.id === agentId);
        const details = (agent?.details ?? []) as IDataObject[];
        return {
          fields: details.map((d) => ({
            id: d.name as string,
            displayName: humanize(d.name as string),
            required: true,
            defaultMatch: false,
            canBeUsedToMatch: false,
            display: true,
            type: 'string',
          })),
          emptyFieldsNotice: agent
            ? 'This agent needs no customer details'
            : 'Choose an agent first',
        };
      },
    },
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const out: INodeExecutionData[] = [];
    let agents: IDataObject[] | undefined; // fetched once, to check required details
    for (let i = 0; i < this.getInputData().length; i++) {
      try {
        const operation = this.getNodeParameter('operation', i) as string;
        let result: IDataObject | IDataObject[];
        if (operation === 'startCall') {
          const details = (this.getNodeParameter('details.value', i, {}) ?? {}) as IDataObject;
          const additional = this.getNodeParameter('additional', i, {}) as IDataObject;
          const agentId = this.getNodeParameter('agentId', i);
          agents ??= (await ymva(this, 'GET', '/agents')) as IDataObject[];
          const needed = (
            (agents.find((a) => a.id === agentId)?.details ?? []) as IDataObject[]
          ).map((d) => d.name as string);
          const missing = [...new Set([...needed, ...Object.keys(details)])].filter(
            (name) => String(details[name] ?? '').trim() === '',
          );
          if (missing.length) {
            // Otherwise the agent would call without them ("Hello, unknown customer").
            throw new NodeOperationError(
              this.getNode(),
              `Fill in the customer details: ${missing.join(', ')}`,
              {
                itemIndex: i,
              },
            );
          }
          const body: IDataObject = {
            agent_id: agentId,
            to: this.getNodeParameter('to', i),
            variables: Object.fromEntries(
              Object.entries(details).map(([name, value]) => [name, String(value).trim()]),
            ),
          };
          const variables = body.variables as IDataObject;
          if (additional.state) variables.state = String(additional.state);
          if (additional.language) variables.preferred_language = String(additional.language);
          if (additional.fromNumberId) body.from_number_id = additional.fromNumberId;
          if (additional.callbackUrl) body.callback_url = additional.callbackUrl;
          result = await ymva(this, 'POST', '/calls/outbound', body);
        } else if (operation === 'getCall') {
          result = await ymva(this, 'GET', `/calls/${this.getNodeParameter('callId', i)}`);
        } else {
          result = await ymva(this, 'GET', '/agents');
        }
        for (const item of Array.isArray(result) ? result : [result]) {
          out.push({ json: item, pairedItem: { item: i } });
        }
      } catch (error) {
        if (this.continueOnFail()) {
          out.push({
            json: { error: (error as Error).message },
            pairedItem: { item: i },
          });
          continue;
        }
        throw error;
      }
    }
    return [out];
  }
}
