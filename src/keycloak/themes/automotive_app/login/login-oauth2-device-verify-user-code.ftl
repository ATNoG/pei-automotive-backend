<#import "template.ftl" as layout>

<@layout.registrationLayout; section>

    <#if section == "form">
        <div id="kc-device-login-form">
            <p class="instruction">Enter the code from your car dashboard</p>

            <form action="${url.oauth2DeviceVerificationAction}" method="post">

                <div class="form-group">

                    <div class="code-input-container" style="display:flex; align-items:center; gap:8px; justify-content:center;">

                        <#list 0..7 as i>
                            <input type="text"
                                   maxlength="1"
                                   class="code-box"
                                   data-index="${i}"
                                   inputmode="text"
                                   pattern="[A-Za-z]" />

                            <#if i == 3>
                                <span style="font-size:20px; font-weight:bold; padding:0 4px;">-</span>
                            </#if>
                        </#list>

                    </div>

                    <!-- Hidden field sent to Keycloak -->
                    <input type="hidden" id="device_code_hidden" name="device_user_code" />

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

        <style>
            .code-input-container {
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 6px;

                max-width: 100%;
                flex-wrap: nowrap;
            }

            .code-box {
                width: 44px;
                height: 44px;
                
                /* Ensure no internal padding is pushing the text to the side */
                padding: 0 !important; 
                margin: 0;

                /* border-box ensures the 44px includes the border thickness */
                box-sizing: border-box;

                font-family: inherit;
                font-size: 22px; /* Slightly larger for readability */
                font-weight: 600;
                text-transform: uppercase;

                /* Center the text horizontally */
                text-align: center; 
                
                /* Center the text vertically */
                line-height: 40px; /* Should be slightly less than height due to 2px borders */

                border: 2px solid #ccc;
                border-radius: 6px;
                background: #fff;
                
                /* Prevent any browser-specific logic from hiding overflow */
                overflow: visible; 
                display: inline-block;
                
                appearance: none;
                -webkit-appearance: none;

                display: flex;
                align-items: center;
                justify-content: center;
            }

            .code-box:focus {
                border-color: #1976d2;
                outline: none;
            }
        </style>

        <script>
        if (!window.deviceCodeInitialized) {
            window.deviceCodeInitialized = true;

            const inputs = document.querySelectorAll('.code-box');
            const hiddenInput = document.getElementById('device_code_hidden');

            inputs.forEach((input, index) => {

                input.addEventListener('input', (e) => {
                    let value = e.target.value.toUpperCase().replace(/[^A-Z]/g, '');
                    e.target.value = value;

                    if (value && index < inputs.length - 1) {
                        inputs[index + 1].focus();
                    }

                    updateHiddenInput();
                });

                input.addEventListener('keydown', (e) => {

                    if (e.key === "Backspace" && !input.value && index > 0) {
                        inputs[index - 1].focus();
                    }

                    if (e.key === "ArrowLeft" && index > 0) {
                        inputs[index - 1].focus();
                    }

                    if (e.key === "ArrowRight" && index < inputs.length - 1) {
                        inputs[index + 1].focus();
                    }
                });

                input.addEventListener('paste', (e) => {
                    e.preventDefault();

                    let paste = (e.clipboardData || window.clipboardData)
                        .getData('text')
                        .toUpperCase()
                        .replace(/[^A-Z]/g, '')
                        .slice(0, 8);

                    inputs.forEach((input, i) => {
                        input.value = paste[i] || '';
                    });

                    updateHiddenInput();
                });
            });

            function updateHiddenInput() {
                const code = Array.from(inputs).map(i => i.value).join('');
                hiddenInput.value = code;
            }

            if (inputs.length > 0) {
                inputs[0].focus();
            }
        }
        </script>

    </#if>

</@layout.registrationLayout>