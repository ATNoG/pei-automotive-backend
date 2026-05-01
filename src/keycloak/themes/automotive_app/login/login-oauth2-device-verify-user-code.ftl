<#import "template.ftl" as layout>

<@layout.registrationLayout; section>

    <#if section == "header">
        <h1 style="color:red;">BLABLABLA RED TEST</h1>

    <#elseif section == "form">
        <div id="kc-device-login-form">
            <p class="instruction">Enter the code from your car dashboard</p>
            
            <form action="${url.oauth2DeviceVerificationAction}" method="post">
                <div class="form-group">
                    <div class="code-input-container">
                        <input id="device_code" name="device_user_code" type="text" 
                               class="aaos-device-input" 
                               placeholder="XXXX-XXXX" 
                               maxlength="9" 
                               autocapitalize="characters" 
                               autocomplete="off" 
                               autofocus />
                    </div>
                    
                    <#if messagesPerField.existsError('device_user_code')>
                        <span id="input-error">
                            ${kcSanitize(messagesPerField.get('device_user_code'))?no_esc}
                        </span>
                    </#if>
                </div>

                <div id="kc-form-buttons">
                    <input class="btn-primary" type="submit" value="Connect App"/>
                </div>
            </form>
        </div>

        <script>
            const input = document.getElementById('device_code');
            if (input) {
                input.addEventListener('input', (e) => {
                    let value = e.target.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
                    if (value.length > 4) {
                        value = value.slice(0, 4) + '-' + value.slice(4, 8);
                    }
                    e.target.value = value;
                });
            }
        </script>
    </#if>

</@layout.registrationLayout>