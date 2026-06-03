package br.com.jksistema.mobile;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.MimeTypeMap;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final String PREFS = "jk_android";
    private static final String PREF_BASE_URL = "base_url";
    private static final String PREF_DOWNLOAD_ID = "download_id";
    private static final String PREF_CURRENT_MODULE = "current_module";

    private FrameLayout root;
    private WebView webView;
    private ProgressBar progressBar;
    private LinearLayout moduleBar;
    private SharedPreferences prefs;
    private String defaultBaseUrl = "";
    private String defaultUpdateManifestUrl = "";
    private String currentBaseUrl = "";
    private String currentModulePath = "estoque.html";
    private boolean allowUserServerEdit = true;
    private boolean updateChecked = false;
    private boolean downloadReceiverRegistered = false;

    private static final ModuleItem[] MODULES = new ModuleItem[] {
            new ModuleItem("Estoque", "estoque.html"),
            new ModuleItem("Cadastro", "cadastro.html"),
            new ModuleItem("Perguntas", "perguntas_pos_venda.html"),
            new ModuleItem("Integracao", "integracoes.html")
    };

    private static class ModuleItem {
        final String label;
        final String path;

        ModuleItem(String label, String path) {
            this.label = label;
            this.path = path;
        }
    }

    private final BroadcastReceiver downloadReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            long expected = prefs.getLong(PREF_DOWNLOAD_ID, -1L);
            long received = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -2L);
            if (expected <= 0 || expected != received) return;
            prefs.edit().remove(PREF_DOWNLOAD_ID).apply();
            openDownloadedApk(received);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        loadConfig();
        root = new FrameLayout(this);
        setContentView(root);
        registerDownloadReceiver();
        String baseUrl = normalizeBaseUrl(prefs.getString(PREF_BASE_URL, defaultBaseUrl));
        if (baseUrl.isEmpty()) {
            showSetupScreen("");
        } else {
            showWebApp(baseUrl);
        }
    }

    @Override
    protected void onDestroy() {
        if (downloadReceiverRegistered) {
            try {
                unregisterReceiver(downloadReceiver);
            } catch (Exception ignored) {
            }
            downloadReceiverRegistered = false;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    private void loadConfig() {
        try (InputStream input = getAssets().open("jk_android_config.json")) {
            String json = readStream(input);
            JSONObject obj = new JSONObject(json);
            defaultBaseUrl = normalizeBaseUrl(obj.optString("appBaseUrl", ""));
            defaultUpdateManifestUrl = obj.optString("updateManifestUrl", "").trim();
            allowUserServerEdit = obj.optBoolean("allowUserServerEdit", true);
        } catch (Exception ignored) {
            defaultBaseUrl = "";
            defaultUpdateManifestUrl = "";
            allowUserServerEdit = true;
        }
    }

    private String readStream(InputStream input) throws Exception {
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString();
    }

    private String normalizeBaseUrl(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) return "";
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        if (!value.contains(".html") && !value.contains("?")) {
            if (value.endsWith("/")) {
                value += "frontend_index.html";
            } else {
                value += "/frontend_index.html";
            }
        }
        return value;
    }

    private String normalizeModulePath(String path) {
        String value = path == null ? "" : path.trim();
        if (value.startsWith("/")) value = value.substring(1);
        for (ModuleItem item : MODULES) {
            if (item.path.equalsIgnoreCase(value)) return item.path;
        }
        return MODULES[0].path;
    }

    private String getOrigin(String url) {
        try {
            URL parsed = new URL(url);
            String port = parsed.getPort() > 0 ? ":" + parsed.getPort() : "";
            return parsed.getProtocol() + "://" + parsed.getHost() + port;
        } catch (Exception ignored) {
            return "";
        }
    }

    private String moduleUrl(String baseUrl, String modulePath) {
        String origin = getOrigin(baseUrl);
        String path = normalizeModulePath(modulePath);
        if (origin.isEmpty()) return baseUrl;
        return origin + "/" + path;
    }

    private String modulePathFromUrl(String url) {
        if (url == null) return "";
        for (ModuleItem item : MODULES) {
            if (url.toLowerCase(Locale.ROOT).contains("/" + item.path.toLowerCase(Locale.ROOT))) {
                return item.path;
            }
        }
        return "";
    }

    private void showSetupScreen(String error) {
        root.removeAllViews();
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.setPadding(dp(24), dp(28), dp(24), dp(24));
        panel.setBackgroundColor(Color.parseColor("#07111F"));

        TextView title = new TextView(this);
        title.setText("JK Sistema Android");
        title.setTextColor(Color.parseColor("#E8F3FF"));
        title.setTextSize(26);
        title.setGravity(Gravity.CENTER);
        title.setTypeface(null, 1);
        panel.addView(title, new LinearLayout.LayoutParams(-1, -2));

        TextView hint = new TextView(this);
        hint.setText("Informe o endereco do servidor JK Sistema. O app carregara Estoque, Cadastro, Perguntas, Integracao e Chat.");
        hint.setTextColor(Color.parseColor("#A9B9CC"));
        hint.setTextSize(15);
        hint.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams hintParams = new LinearLayout.LayoutParams(-1, -2);
        hintParams.setMargins(0, dp(12), 0, dp(20));
        panel.addView(hint, hintParams);

        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(prefs.getString(PREF_BASE_URL, defaultBaseUrl));
        input.setHint("Ex.: http://192.168.0.10:8001");
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.parseColor("#7890A8"));
        input.setPadding(dp(14), 0, dp(14), 0);
        input.setBackgroundColor(Color.parseColor("#0D1B2E"));
        panel.addView(input, new LinearLayout.LayoutParams(-1, dp(52)));

        if (error != null && !error.isEmpty()) {
            TextView errorView = new TextView(this);
            errorView.setText(error);
            errorView.setTextColor(Color.parseColor("#FF6B72"));
            errorView.setTextSize(13);
            LinearLayout.LayoutParams errParams = new LinearLayout.LayoutParams(-1, -2);
            errParams.setMargins(0, dp(10), 0, 0);
            panel.addView(errorView, errParams);
        }

        Button connect = new Button(this);
        connect.setText("Conectar");
        connect.setAllCaps(false);
        connect.setTextColor(Color.WHITE);
        connect.setBackgroundColor(Color.parseColor("#1E9BFF"));
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(-1, dp(52));
        buttonParams.setMargins(0, dp(16), 0, 0);
        panel.addView(connect, buttonParams);

        TextView footer = new TextView(this);
        footer.setText("Os modulos usam as mesmas telas e APIs do sistema. O backend precisa estar acessivel pela rede ou em servidor.");
        footer.setTextColor(Color.parseColor("#A9B9CC"));
        footer.setTextSize(12);
        footer.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams footerParams = new LinearLayout.LayoutParams(-1, -2);
        footerParams.setMargins(0, dp(20), 0, 0);
        panel.addView(footer, footerParams);

        connect.setOnClickListener(v -> {
            String url = normalizeBaseUrl(input.getText().toString());
            if (url.isEmpty()) {
                showSetupScreen("Informe o endereco do servidor.");
                return;
            }
            prefs.edit().putString(PREF_BASE_URL, url).apply();
            showWebApp(url);
        });

        root.addView(panel, new FrameLayout.LayoutParams(-1, -1));
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void showWebApp(String baseUrl) {
        currentBaseUrl = baseUrl;
        currentModulePath = normalizeModulePath(prefs.getString(PREF_CURRENT_MODULE, MODULES[0].path));
        root.removeAllViews();
        webView = new WebView(this);
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.VISIBLE);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(false);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                progressBar.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri == null ? "" : uri.getScheme();
                if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
                    return false;
                }
                openExternal(uri);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();
                String modulePath = modulePathFromUrl(url);
                if (!modulePath.isEmpty()) {
                    currentModulePath = modulePath;
                    prefs.edit().putString(PREF_CURRENT_MODULE, modulePath).apply();
                    updateModuleSelection();
                }
                injectAndroidRuntimeHelpers();
                if (!updateChecked) {
                    updateChecked = true;
                    checkForUpdates(baseUrl);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    String message = "Nao foi possivel carregar o servidor JK Sistema.";
                    if (allowUserServerEdit) {
                        showSetupScreen(message);
                    } else {
                        Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show();
                    }
                }
            }
        });
        webView.setDownloadListener(createDownloadListener());

        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(Color.parseColor("#07111F"));
        root.addView(shell, new FrameLayout.LayoutParams(-1, -1));

        shell.addView(createModuleNavigation(), new LinearLayout.LayoutParams(-1, dp(58)));

        FrameLayout webHost = new FrameLayout(this);
        shell.addView(webHost, new LinearLayout.LayoutParams(-1, 0, 1f));

        FrameLayout.LayoutParams webParams = new FrameLayout.LayoutParams(-1, -1);
        webHost.addView(webView, webParams);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(-1, dp(3));
        progressParams.gravity = Gravity.TOP;
        webHost.addView(progressBar, progressParams);
        loadModule(currentModulePath);
    }

    private View createModuleNavigation() {
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.parseColor("#07111F"));

        moduleBar = new LinearLayout(this);
        moduleBar.setOrientation(LinearLayout.HORIZONTAL);
        moduleBar.setGravity(Gravity.CENTER_VERTICAL);
        moduleBar.setPadding(dp(8), dp(7), dp(8), dp(7));

        for (ModuleItem item : MODULES) {
            Button button = createModuleButton(item.label);
            button.setTag(item.path);
            button.setOnClickListener(v -> loadModule(item.path));
            moduleBar.addView(button, new LinearLayout.LayoutParams(dp(118), -1));
        }

        Button chat = createModuleButton("Chat");
        chat.setTag("chat");
        chat.setTextColor(Color.WHITE);
        chat.setBackgroundColor(Color.parseColor("#139D89"));
        chat.setOnClickListener(v -> openChatSidebar());
        moduleBar.addView(chat, new LinearLayout.LayoutParams(dp(92), -1));

        Button server = createModuleButton("Servidor");
        server.setTag("server");
        server.setTextColor(Color.parseColor("#DCEBFF"));
        server.setBackgroundColor(Color.parseColor("#182A42"));
        server.setOnClickListener(v -> showSetupScreen(""));
        moduleBar.addView(server, new LinearLayout.LayoutParams(dp(108), -1));

        scroll.addView(moduleBar, new HorizontalScrollView.LayoutParams(-2, -1));
        updateModuleSelection();
        return scroll;
    }

    private Button createModuleButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(13);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(8), 0, dp(8), 0);
        button.setTextColor(Color.parseColor("#DCEBFF"));
        button.setBackgroundColor(Color.parseColor("#10233A"));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(112), -1);
        params.setMargins(dp(4), 0, dp(4), 0);
        button.setLayoutParams(params);
        return button;
    }

    private void updateModuleSelection() {
        if (moduleBar == null) return;
        for (int i = 0; i < moduleBar.getChildCount(); i += 1) {
            View child = moduleBar.getChildAt(i);
            if (!(child instanceof Button)) continue;
            Button button = (Button) child;
            Object tag = button.getTag();
            boolean selected = tag != null && String.valueOf(tag).equalsIgnoreCase(currentModulePath);
            if ("chat".equals(tag)) {
                button.setBackgroundColor(Color.parseColor("#139D89"));
                button.setTextColor(Color.WHITE);
            } else if ("server".equals(tag)) {
                button.setBackgroundColor(Color.parseColor("#182A42"));
                button.setTextColor(Color.parseColor("#DCEBFF"));
            } else if (selected) {
                button.setBackgroundColor(Color.parseColor("#1E9BFF"));
                button.setTextColor(Color.WHITE);
            } else {
                button.setBackgroundColor(Color.parseColor("#10233A"));
                button.setTextColor(Color.parseColor("#DCEBFF"));
            }
        }
    }

    private void loadModule(String modulePath) {
        if (webView == null) return;
        currentModulePath = normalizeModulePath(modulePath);
        prefs.edit().putString(PREF_CURRENT_MODULE, currentModulePath).apply();
        updateModuleSelection();
        webView.loadUrl(moduleUrl(currentBaseUrl, currentModulePath));
    }

    private void openChatSidebar() {
        if (webView == null) return;
        injectAndroidRuntimeHelpers();
        String script = "(function(){"
                + "function abrir(){"
                + "var panel=document.getElementById('jk-ia-panel');"
                + "if(panel){panel.classList.add('aberto');var fab=document.getElementById('jk-ia-fab');if(fab){fab.textContent='x';}return true;}"
                + "var fab=document.getElementById('jk-ia-fab');if(fab){fab.click();return true;}"
                + "var global=document.getElementById('jk-global-ai-sidebar');"
                + "if(global){global.classList.add('open');try{localStorage.setItem('jk-global-ai-sidebar-open','true');}catch(e){}return true;}"
                + "return false;"
                + "}"
                + "if(abrir()) return 'opened';"
                + "if(!document.querySelector('script[src*=\"ia-sidebar.js\"]')){var s=document.createElement('script');s.src='/ia-sidebar.js?v=android';document.body.appendChild(s);}"
                + "setTimeout(abrir,900);"
                + "return 'loading';"
                + "})();";
        webView.evaluateJavascript(script, value -> Toast.makeText(this, "Chat aberto no modulo atual.", Toast.LENGTH_SHORT).show());
    }

    private void injectAndroidRuntimeHelpers() {
        if (webView == null) return;
        String script = "(function(){"
                + "if(document.getElementById('jk-android-runtime-style')) return;"
                + "var style=document.createElement('style');"
                + "style.id='jk-android-runtime-style';"
                + "style.textContent='html,body{overscroll-behavior:contain;} body{min-width:0!important;} #jk-ia-panel{max-width:calc(100vw - 18px)!important;width:min(390px,calc(100vw - 18px))!important;} #jk-ia-fab{bottom:18px!important;right:18px!important;} #jk-global-ai-sidebar{max-width:calc(100vw - 46px)!important;}';"
                + "document.head.appendChild(style);"
                + "})();";
        webView.evaluateJavascript(script, null);
    }

    private DownloadListener createDownloadListener() {
        return (url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                startActivity(intent);
            } catch (Exception ignored) {
                Toast.makeText(this, "Download iniciado no navegador externo.", Toast.LENGTH_SHORT).show();
            }
        };
    }

    private void registerDownloadReceiver() {
        if (downloadReceiverRegistered) return;
        try {
            IntentFilter filter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
            if (android.os.Build.VERSION.SDK_INT >= 33) {
                registerReceiver(downloadReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(downloadReceiver, filter);
            }
            downloadReceiverRegistered = true;
        } catch (Exception err) {
            downloadReceiverRegistered = false;
        }
    }

    private void openExternal(Uri uri) {
        if (uri == null) return;
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, uri);
            startActivity(intent);
        } catch (Exception ignored) {
            Toast.makeText(this, "Nao foi possivel abrir este link.", Toast.LENGTH_SHORT).show();
        }
    }

    private void checkForUpdates(String baseUrl) {
        String manifestUrl = defaultUpdateManifestUrl;
        if (manifestUrl.isEmpty()) {
            String origin = getOrigin(baseUrl);
            if (!origin.isEmpty()) {
                manifestUrl = origin
                        + "/api/mobile-update/check?platform=android&version="
                        + BuildConfig.VERSION_NAME
                        + "&versionCode="
                        + BuildConfig.VERSION_CODE;
            }
        }
        if (manifestUrl.isEmpty()) return;
        final String finalManifestUrl = manifestUrl;
        new Thread(() -> {
            try {
                HttpURLConnection connection = (HttpURLConnection) new URL(finalManifestUrl).openConnection();
                connection.setConnectTimeout(7000);
                connection.setReadTimeout(9000);
                connection.setRequestProperty("Accept", "application/json");
                int status = connection.getResponseCode();
                if (status < 200 || status >= 300) return;
                String json = readStream(connection.getInputStream());
                JSONObject obj = new JSONObject(json);
                int remoteCode = obj.optInt("versionCode", 0);
                String remoteVersion = obj.optString("version", "");
                String apkUrl = obj.optString("apkUrl", obj.optString("url", ""));
                boolean update = obj.optBoolean("updateAvailable", false)
                        || remoteCode > BuildConfig.VERSION_CODE
                        || isRemoteVersionNewer(remoteVersion, BuildConfig.VERSION_NAME);
                if (!update || apkUrl.trim().isEmpty()) return;
                String notes = obj.optString("notes", "");
                boolean required = obj.optBoolean("required", false);
                runOnUiThread(() -> showUpdateDialog(remoteVersion, apkUrl, notes, required));
            } catch (Exception ignored) {
            }
        }).start();
    }

    private boolean isRemoteVersionNewer(String remote, String current) {
        if (remote == null || remote.trim().isEmpty()) return false;
        String[] a = remote.trim().split("\\.");
        String[] b = current == null ? new String[0] : current.trim().split("\\.");
        int max = Math.max(a.length, b.length);
        for (int i = 0; i < max; i++) {
            int av = i < a.length ? parseInt(a[i]) : 0;
            int bv = i < b.length ? parseInt(b[i]) : 0;
            if (av > bv) return true;
            if (av < bv) return false;
        }
        return false;
    }

    private int parseInt(String raw) {
        try {
            return Integer.parseInt(raw.replaceAll("[^0-9]", ""));
        } catch (Exception ignored) {
            return 0;
        }
    }

    private void showUpdateDialog(String version, String apkUrl, String notes, boolean required) {
        String title = version == null || version.isEmpty()
                ? "Atualizacao disponivel"
                : "Atualizacao " + version + " disponivel";
        String message = notes == null || notes.isEmpty()
                ? "Uma nova versao do JK Sistema Android esta pronta para instalar."
                : notes;
        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton("Baixar e instalar", (dialog, which) -> downloadUpdate(apkUrl));
        if (!required) {
            builder.setNegativeButton("Depois", null);
        }
        builder.show();
    }

    private void downloadUpdate(String apkUrl) {
        if (apkUrl == null || apkUrl.trim().isEmpty()) return;
        try {
            if (android.os.Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
                Intent intent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
                intent.setData(Uri.parse("package:" + getPackageName()));
                startActivity(intent);
                Toast.makeText(this, "Ative a permissao e toque em atualizar novamente.", Toast.LENGTH_LONG).show();
                return;
            }
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(apkUrl));
            request.setTitle("JK Sistema Android");
            request.setDescription("Baixando atualizacao");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setAllowedOverMetered(true);
            request.setAllowedOverRoaming(true);
            String extension = MimeTypeMap.getFileExtensionFromUrl(apkUrl);
            if (extension == null || !extension.toLowerCase(Locale.ROOT).contains("apk")) {
                extension = "apk";
            }
            request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, "jk-sistema-update." + extension);
            DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            long id = manager.enqueue(request);
            prefs.edit().putLong(PREF_DOWNLOAD_ID, id).apply();
            Toast.makeText(this, "Atualizacao sendo baixada.", Toast.LENGTH_LONG).show();
        } catch (Exception err) {
            Toast.makeText(this, "Falha ao baixar atualizacao: " + err.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void openDownloadedApk(long downloadId) {
        try {
            DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            Uri uri = manager.getUriForDownloadedFile(downloadId);
            if (uri == null) {
                Toast.makeText(this, "Atualizacao baixada, mas o arquivo nao foi localizado.", Toast.LENGTH_LONG).show();
                return;
            }
            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(uri, "application/vnd.android.package-archive");
            install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(install);
        } catch (Exception err) {
            Toast.makeText(this, "Abra o APK baixado para concluir a atualizacao.", Toast.LENGTH_LONG).show();
        }
    }

    private int dp(int value) {
        float density = getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }
}
