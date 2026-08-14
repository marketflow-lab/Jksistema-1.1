import { QUESTION_SUGGESTION_TEMPLATE_NAME } from "./question-templates";
import { Env, graphRequest, json, JsonRecord, nowSeconds, requestJson } from "./shared";

export const TEMPLATE_DEFINITIONS = [
  {
    name: "jk_joao_tarefa_concluida",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Joao Pretinho concluiu sua solicitacao: {{1}}. Abra o JK Sistema para consultar o resultado completo.", example: { body_text: [["consulta concluida"]] } }],
  },
  {
    name: "jk_joao_aprovacao_pendente",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Joao Pretinho recebeu um pedido que exige aprovacao: {{1}}. Abra o JK Sistema para revisar.", example: { body_text: [["ajuste solicitado"]] } }],
  },
  {
    name: "jk_joao_alerta_operacional",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "Alerta {{1}} do Joao Pretinho: {{2}}. Consulte o JK Sistema para detalhes.", example: { body_text: [["critico", "risco operacional detectado"]] } }],
  },
  {
    name: "jk_black_jhon_nova_pergunta",
    category: "UTILITY",
    language: "pt_BR",
    components: [{ type: "BODY", text: "O Black Jhon encontrou uma nova pergunta de comprador na loja {{1}}. Abra esta conversa para revisar a resposta sugerida.", example: { body_text: [["JK Pecas"]] } }],
  },
  {
    name: QUESTION_SUGGESTION_TEMPLATE_NAME,
    category: "UTILITY",
    language: "pt_BR",
    components: [
      {
        type: "BODY",
        text: "O Black Jhon encontrou uma nova pergunta de comprador na loja {{1}}.\n\nPergunta: {{2}}\n\nSugestao de resposta: {{3}}\n\nToque em Ver sugestao para aprovar, corrigir, gerar outra resposta ou negar.",
        example: { body_text: [["JK Pecas", "Este produto tem garantia?", "Sim, o produto possui garantia conforme as condicoes do anuncio."]] },
      },
      { type: "BUTTONS", buttons: [{ type: "QUICK_REPLY", text: "Ver sugestao" }] },
    ],
  },
];

export async function syncTemplates(request: Request, env: Env): Promise<Response> {
  const body = await requestJson(request);
  const createMissing = body.create_missing === true;
  if (createMissing) {
    for (const definition of TEMPLATE_DEFINITIONS) {
      await graphRequest(env, `${env.META_WABA_ID}/message_templates`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(definition) });
    }
  }
  const response = await graphRequest(env, `${env.META_WABA_ID}/message_templates?fields=name,status,category,language,components&limit=100`);
  const payload = (await response.json()) as JsonRecord;
  if (!response.ok) return json({ success: false, error: payload }, 502);
  const now = nowSeconds();
  let synced = 0;
  for (const raw of Array.isArray(payload.data) ? payload.data : []) {
    const item = (raw || {}) as JsonRecord;
    const name = String(item.name || "");
    if (!TEMPLATE_DEFINITIONS.some((definition) => definition.name === name)) continue;
    await env.DB.prepare("INSERT INTO template_registry(name,language,category,status,components_json,last_verified_at) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET language=excluded.language,category=excluded.category,status=excluded.status,components_json=excluded.components_json,last_verified_at=excluded.last_verified_at")
      .bind(name, String(item.language || "pt_BR"), String(item.category || ""), String(item.status || ""), JSON.stringify(item.components || []), now).run();
    synced += 1;
  }
  return json({ success: true, synced });
}
