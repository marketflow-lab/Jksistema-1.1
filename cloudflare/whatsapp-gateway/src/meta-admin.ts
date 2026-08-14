import { profileImagePolicy } from "./core";
import { audit, Env, graphRequest, graphUploadRequest, json, JsonRecord, responsePayload, sanitizeMetaPayload } from "./shared";

export function profilePictureUrl(payload: JsonRecord): string {
  const data = Array.isArray(payload.data) ? payload.data : [];
  for (const raw of data) {
    const item = (raw || {}) as JsonRecord;
    const direct = String(item.profile_picture_url || "");
    if (direct) return direct;
    const nested = (item.business_profile || {}) as JsonRecord;
    const nestedUrl = String(nested.profile_picture_url || "");
    if (nestedUrl) return nestedUrl;
  }
  return "";
}

export async function businessProfile(env: Env): Promise<Response> {
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  if (!/^\d+$/.test(phoneId)) return json({ success: false, error: "META_PHONE_NUMBER_ID_missing" }, 503);
  const response = await graphRequest(
    env,
    `${phoneId}/whatsapp_business_profile?fields=about,address,description,email,profile_picture_url,websites,vertical`,
  );
  const payload = await responsePayload(response);
  if (!response.ok) return json({ success: false, error: "meta_profile_read_failed", meta: payload }, 502);
  return json({ success: true, profile: payload, profile_picture_url: profilePictureUrl(payload) });
}

export async function metaCallingStatus(env: Env): Promise<Response> {
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  const wabaId = String(env.META_WABA_ID || "").trim();
  if (!/^\d+$/.test(phoneId) || !env.META_SYSTEM_USER_TOKEN) {
    return json({ success: false, error: "meta_calling_configuration_missing" }, 503);
  }

  const [phoneResponse, settingsResponse, wabaResponse, wabaPhonesResponse] = await Promise.all([
    graphRequest(
      env,
      `${phoneId}?fields=display_phone_number,verified_name,quality_rating,name_status,platform_type,code_verification_status`,
    ),
    graphRequest(env, `${phoneId}/settings?include_sip_credentials=false`),
    /^\d+$/.test(wabaId)
      ? graphRequest(env, `${wabaId}?fields=id,name,account_review_status,owner_business_info`)
      : Promise.resolve(new Response(JSON.stringify({ error: "META_WABA_ID_missing" }), { status: 503 })),
    graphRequest(env, `${phoneId}?fields=id,whatsapp_business_manager_messaging_limit`),
  ]);
  const [phonePayload, settingsPayload, wabaPayload, wabaPhonesPayload] = await Promise.all([
    responsePayload(phoneResponse),
    responsePayload(settingsResponse),
    responsePayload(wabaResponse),
    responsePayload(wabaPhonesResponse),
  ]);
  const ownerBusinessInfo = (wabaPayload.owner_business_info || {}) as JsonRecord;
  const ownerBusinessId = String(ownerBusinessInfo.id || "").trim();
  const ownerBusinessResponse = /^\d+$/.test(ownerBusinessId)
    ? await graphRequest(env, `${ownerBusinessId}?fields=id,name,verification_status`)
    : new Response(JSON.stringify({ error: "owner_business_id_missing" }), { status: 503 });
  const ownerBusinessPayload = await responsePayload(ownerBusinessResponse);
  await audit(env, "meta_calling_status_checked", "", {
    phone_status: phoneResponse.status,
    settings_status: settingsResponse.status,
    waba_status: wabaResponse.status,
    waba_phones_status: wabaPhonesResponse.status,
    owner_business_status: ownerBusinessResponse.status,
  });

  return json({
    success: phoneResponse.ok && settingsResponse.ok,
    graph_api_version: String(env.META_GRAPH_API_VERSION || ""),
    phone: {
      available: phoneResponse.ok,
      http_status: phoneResponse.status,
      data: sanitizeMetaPayload(phonePayload),
    },
    calling: {
      available: settingsResponse.ok,
      http_status: settingsResponse.status,
      data: sanitizeMetaPayload(settingsPayload),
    },
    eligibility: {
      waba: {
        available: wabaResponse.ok,
        http_status: wabaResponse.status,
        data: sanitizeMetaPayload(wabaPayload),
      },
      phone_limits: {
        available: wabaPhonesResponse.ok,
        http_status: wabaPhonesResponse.status,
        data: sanitizeMetaPayload(wabaPhonesPayload),
      },
      owner_business: {
        available: ownerBusinessResponse.ok,
        http_status: ownerBusinessResponse.status,
        data: sanitizeMetaPayload(ownerBusinessPayload),
      },
    },
  }, phoneResponse.ok && settingsResponse.ok ? 200 : 502);
}

export async function prepareMetaCallingSip(env: Env): Promise<Response> {
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  const sipHost = String(env.VOICE_SIP_HOST || "").trim().toLowerCase();
  if (!/^\d+$/.test(phoneId) || !env.META_SYSTEM_USER_TOKEN) {
    return json({ success: false, error: "meta_calling_configuration_missing" }, 503);
  }
  if (!/^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(sipHost)) {
    return json({ success: false, error: "voice_sip_host_invalid" }, 503);
  }

  const settings = {
    calling: {
      status: "DISABLED",
      call_icon_visibility: "DISABLE_ALL",
      callback_permission_status: "DISABLED",
      sip: {
        status: "ENABLED",
        servers: [{ hostname: sipHost, port: 5061 }],
      },
      srtp_key_exchange_protocol: "SDES",
    },
  };
  const updateResponse = await graphRequest(env, `${phoneId}/settings`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(settings),
  });
  const updatePayload = await responsePayload(updateResponse);
  if (!updateResponse.ok) {
    await audit(env, "meta_calling_sip_prepare_failed", "", { status: updateResponse.status });
    return json({
      success: false,
      error: "meta_calling_sip_prepare_failed",
      http_status: updateResponse.status,
      meta: sanitizeMetaPayload(updatePayload),
    }, 502);
  }

  const verifyResponse = await graphRequest(env, `${phoneId}/settings?include_sip_credentials=false`);
  const verifyPayload = await responsePayload(verifyResponse);
  await audit(env, "meta_calling_sip_prepared", "", {
    status: updateResponse.status,
    verify_status: verifyResponse.status,
    sip_host: sipHost,
    sip_port: 5061,
    calling_enabled: false,
  });
  return json({
    success: verifyResponse.ok,
    prepared: true,
    calling_enabled: false,
    sip_host: sipHost,
    sip_port: 5061,
    update: sanitizeMetaPayload(updatePayload),
    settings: sanitizeMetaPayload(verifyPayload),
  }, verifyResponse.ok ? 200 : 502);
}

export async function updateProfilePhoto(request: Request, env: Env): Promise<Response> {
  const contentType = String(request.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
  const fileNameRaw = String(request.headers.get("x-file-name") || "black-jhon-profile.jpg").trim();
  const fileName = fileNameRaw.replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || "black-jhon-profile.jpg";
  const file = await request.arrayBuffer();
  const policy = profileImagePolicy(contentType, file.byteLength, new Uint8Array(file.slice(0, 12)));
  if (!policy.allowed) return json({ success: false, error: policy.error }, 400);

  const appId = String(env.META_APP_ID || "").trim();
  const phoneId = String(env.META_PHONE_NUMBER_ID || "").trim();
  if (!/^\d+$/.test(appId) || !/^\d+$/.test(phoneId) || !env.META_SYSTEM_USER_TOKEN) {
    return json({ success: false, error: "meta_profile_configuration_missing" }, 503);
  }

  const sessionResponse = await graphRequest(
    env,
    `${appId}/uploads?file_name=${encodeURIComponent(fileName)}&file_length=${file.byteLength}&file_type=${encodeURIComponent(contentType)}`,
    { method: "POST" },
  );
  const sessionPayload = await responsePayload(sessionResponse);
  const uploadId = String(sessionPayload.id || "");
  if (!sessionResponse.ok || !uploadId.startsWith("upload:")) {
    return json({ success: false, stage: "create_upload_session", error: sessionPayload }, 502);
  }

  const uploadResponse = await graphUploadRequest(env, uploadId, {
    method: "POST",
    headers: { "content-type": contentType, "file_offset": "0" },
    body: file,
  });
  const uploadPayload = await responsePayload(uploadResponse);
  const handle = String(uploadPayload.h || "");
  if (!uploadResponse.ok || !handle) {
    return json({ success: false, stage: "upload_file", error: uploadPayload }, 502);
  }

  const updateResponse = await graphRequest(env, `${phoneId}/whatsapp_business_profile`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", profile_picture_handle: handle }),
  });
  const updatePayload = await responsePayload(updateResponse);
  if (!updateResponse.ok) {
    return json({ success: false, stage: "update_business_profile", error: updatePayload }, 502);
  }

  const verifyResponse = await graphRequest(env, `${phoneId}/whatsapp_business_profile?fields=profile_picture_url`);
  const verifyPayload = await responsePayload(verifyResponse);
  const digest = await crypto.subtle.digest("SHA-256", file);
  const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
  await audit(env, "business_profile_photo_updated", "", { file_name: fileName, mime: contentType, bytes: file.byteLength, sha256 });
  return json({
    success: true,
    updated: true,
    verified: verifyResponse.ok && Boolean(profilePictureUrl(verifyPayload)),
    profile_picture_url: profilePictureUrl(verifyPayload),
    image: { file_name: fileName, mime: contentType, bytes: file.byteLength, sha256 },
  });
}

export async function finalizeMetaWebhook(request: Request, env: Env): Promise<Response> {
  const appId = String(env.META_APP_ID || "").trim();
  if (!/^\d+$/.test(appId)) return json({ success: false, stage: "configuration", error: "META_APP_ID_missing" }, 503);
  if (!env.META_APP_SECRET || !env.META_SYSTEM_USER_TOKEN || !env.META_VERIFY_TOKEN || !env.META_WABA_ID) {
    return json({ success: false, stage: "configuration", error: "meta_secrets_missing" }, 503);
  }
  const version = String(env.META_GRAPH_API_VERSION || "").trim();
  const callbackUrl = `${new URL(request.url).origin}/webhooks/whatsapp`;

  const tokenBody = new URLSearchParams({
    client_id: appId,
    client_secret: env.META_APP_SECRET,
    grant_type: "client_credentials",
  });
  const tokenResponse = await fetch(`https://graph.facebook.com/${version}/oauth/access_token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: tokenBody,
  });
  const tokenPayload = await responsePayload(tokenResponse);
  const appAccessToken = String(tokenPayload.access_token || "");
  if (!tokenResponse.ok || !appAccessToken) {
    return json({ success: false, stage: "app_access_token", meta: tokenPayload }, 502);
  }

  const appSubscriptionBody = new URLSearchParams({
    object: "whatsapp_business_account",
    callback_url: callbackUrl,
    verify_token: env.META_VERIFY_TOKEN,
    fields: "messages",
    access_token: appAccessToken,
  });
  const appSubscriptionResponse = await fetch(`https://graph.facebook.com/${version}/${appId}/subscriptions`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: appSubscriptionBody,
  });
  const appSubscription = await responsePayload(appSubscriptionResponse);
  if (!appSubscriptionResponse.ok) {
    return json({ success: false, stage: "app_messages_subscription", meta: appSubscription }, 502);
  }

  const wabaSubscriptionResponse = await graphRequest(env, `${env.META_WABA_ID}/subscribed_apps`, { method: "POST" });
  const wabaSubscription = await responsePayload(wabaSubscriptionResponse);
  if (!wabaSubscriptionResponse.ok) {
    return json({ success: false, stage: "waba_subscription", meta: wabaSubscription }, 502);
  }
  const subscriptionsResponse = await graphRequest(env, `${env.META_WABA_ID}/subscribed_apps`);
  const subscriptions = await responsePayload(subscriptionsResponse);
  if (!subscriptionsResponse.ok) {
    return json({ success: false, stage: "waba_subscription_check", meta: subscriptions }, 502);
  }
  await audit(env, "meta_webhook_finalized", appId, { actor: "bridge", callback_url: callbackUrl });
  return json({
    success: true,
    callback_url: callbackUrl,
    app_subscription: appSubscription,
    waba_subscription: wabaSubscription,
    subscriptions,
  });
}
