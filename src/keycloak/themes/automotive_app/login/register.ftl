<#import "template.ftl" as layout>
<@layout.registrationLayout pageTitle=msg("registerTitle") displayMessage=!messagesPerField.existsError('firstName','lastName','email','username','password','password-confirm','termsAccepted'); section>
    <#if section = "header">
        ${msg("registerTitle")}
    <#elseif section = "form">
        <form id="kc-register-form" action="${url.registrationAction}" method="post">
            
            <#-- Input Fields (Required attribute added for browser-level validation) -->
            <div class="${properties.kcFormGroupClass!}">
                <label for="firstName" class="${properties.kcLabelClass!}">${msg("firstName")}</label>
                <input type="text" id="firstName" class="${properties.kcInputClass!}" name="firstName" value="${(register.formData.firstName!'')}" required />
            </div>

            <div class="${properties.kcFormGroupClass!}">
                <label for="lastName" class="${properties.kcLabelClass!}">${msg("lastName")}</label>
                <input type="text" id="lastName" class="${properties.kcInputClass!}" name="lastName" value="${(register.formData.lastName!'')}" required />
            </div>

            <div class="${properties.kcFormGroupClass!}">
                <label for="email" class="${properties.kcLabelClass!}">${msg("email")}</label>
                <input type="email" id="email" class="${properties.kcInputClass!}" name="email" value="${(register.formData.email!'')}" autocomplete="email" required />
            </div>

            <#if !realm.registrationEmailAsUsername>
                <div class="${properties.kcFormGroupClass!}">
                    <label for="username" class="${properties.kcLabelClass!}">${msg("username")}</label>
                    <input type="text" id="username" class="${properties.kcInputClass!}" name="username" value="${(register.formData.username!'')}" autocomplete="username" required />
                </div>
            </#if>

            <div class="${properties.kcFormGroupClass!}">
                <label for="password" class="${properties.kcLabelClass!}">${msg("password")}</label>
                <input type="password" id="password" class="${properties.kcInputClass!}" name="password" autocomplete="new-password" required />
            </div>

            <div class="${properties.kcFormGroupClass!}">
                <label for="password-confirm" class="${properties.kcLabelClass!}">${msg("passwordConfirm")}</label>
                <input type="password" id="password-confirm" class="${properties.kcInputClass!}" name="password-confirm" required />
            </div>

            <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">

            <#-- Centered Terms Checkbox -->
            <div class="${properties.kcFormGroupClass!}" style="display: flex; justify-content: center; text-align: center;">
                <div class="checkbox" style="display: inline-flex; align-items: center; gap: 8px;">
                    <input type="checkbox" id="termsAccepted" name="termsAccepted" value="true" />
                    <label for="termsAccepted" style="font-size: 13px; font-weight: normal; color: #555; margin-bottom: 0;">
                        I agree to the <a href="javascript:void(0)" onclick="toggleModal()" style="color: #007bff; text-decoration: underline;">Terms and Conditions</a>.
                    </label>
                </div>
            </div>

            <#-- Form Buttons -->
            <div id="kc-form-buttons" style="margin-top: 24px;">
                <input id="kc-register-button" class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonBlockClass!} ${properties.kcButtonLargeClass!}" type="submit" value="${msg("doRegister")}" disabled />
                
                <div style="text-align: center; margin-top: 16px;">
                    <span><a href="${url.loginUrl}" style="font-size: 14px; text-decoration: underline; color: #007bff;">${kcSanitize(msg("backToLogin"))?no_esc}</a></span>
                </div>
            </div>
        </form>

        <#-- Terms and Conditions Modal with Full Text -->
        <div id="termsModal" style="display:none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.8); padding: 15px; box-sizing: border-box; backdrop-filter: blur(5px);">
            <div style="background-color: white; margin: 2% auto; padding: 25px; border-radius: 12px; max-width: 650px; height: 90vh; display: flex; flex-direction: column; position: relative; box-shadow: 0 15px 35px rgba(0,0,0,0.3);">
                
                <span onclick="toggleModal()" style="position: absolute; right: 20px; top: 15px; cursor: pointer; font-size: 32px; color: #aaa; line-height: 1;">&times;</span>
                
                <h2 style="margin-top: 0; color: #111; font-size: 20px; border-bottom: 2px solid #007bff; padding-bottom: 10px;">Legal Information</h2>

                <div style="flex-grow: 1; overflow-y: auto; margin: 15px 0; padding-right: 15px; text-align: left; font-size: 14px; line-height: 1.6; color: #333; font-family: sans-serif;">
                    <p><strong>By explicitly consenting to these terms within the App, you agree to the following conditions.</strong></p>
                    
                    <h3 style="color: #007bff; border-bottom: 1px solid #eee;">Terms Of Use</h3>
                    
                    <p><strong>1. User Eligibility</strong><br>
                    To use this Service, you must meet the following requirements:<br>
                    <strong>Age and Licensing:</strong> You must be at least 18 years old and hold a valid driver's license to use this Service. By using the App, you confirm that you meet these requirements.<br>
                    <strong>Emergency Use:</strong> This application is built for standard civilian commuting. It has <u>not</u> been tested, certified, or authorized for use by emergency vehicles (e.g., ambulances, police, fire departments) acting in an official capacity.</p>

                    <p><strong>2. Critical Safety Warnings</strong><br>
                    Your primary responsibility is safe driving.<br>
                    <strong>Road Information Prevails:</strong> This App is a driving aid. Actual road conditions, traffic signs, police instructions, and traffic laws always supersede any information or alerts provided by the App.<br>
                    <strong>No Reliance on Maneuver Coordination:</strong> Features such as overtaking alerts or highway entry instructions are strictly for situational awareness. You must <u>not</u> rely solely on the App to execute maneuvers. You are legally obligated to check your mirrors and blind spots.<br>
                    <strong>Offline and Connectivity Limitations:</strong> The App relies on continuous internet connectivity. In scenarios where internet connectivity is not guaranteed, the App operates on a fail-silent basis and may not display any alerts or maneuver coordination instructions.<br>
                    <strong>Interface Interaction:</strong> Because this App runs natively on Android Automotive OS, you must only interact with the interface when it is safe and legal to do so.<br>
                    <strong>System Limitations:</strong> The Service relies on experimental IoT sensor data (DT4MOB) and digital twin infrastructure (Eclipse Ditto). Network latency, sensor failures, or environmental factors may cause alerts (e.g., speeding, overtaking, hazards) to be delayed and/or inaccurate.</p>

                    <p><strong>3. License and Restrictions</strong><br>
                    You are granted a free, non-exclusive, revocable license to use the App for personal, non-commercial use. You may not:</p>
                    <ul>
                        <li>Reverse engineer, decompile, or copy the App or its backend infrastructure.</li>
                        <li>Systematically scrape or extract data from the ITAv sensor network or Mosquitto message brokers.</li>
                        <li>Use the App for commercial fleet tracking or any illegal purposes.</li>
                    </ul>

                    <p><strong>4. Limitation of Liability</strong><br>
                    We strive to provide a helpful and innovative academic tool, but the Service is provided "as is" without commercial warranties of uninterrupted performance or absolute accuracy. To the maximum extent permitted by applicable EU and Portuguese law, the Universidade de Aveiro, ITAv, and the development team shall not be liable for indirect damages, loss of profits, or damages resulting from your driving decisions, system latency, or sensor inaccuracies. Nothing in these terms limits liability for gross negligence, willful misconduct, or personal injury where mandated by law.</p>

                    <p><strong>5. Service Modifications and Project Lifespan</strong><br>
                    As an academic research project, the App, backend microservices, and sensor data streams may be modified, suspended, or permanently discontinued at any time without prior notice, particularly at the conclusion of the academic term.</p>

                    <h3 style="color: #007bff; border-bottom: 1px solid #eee; margin-top: 30px;">Privacy Policy</h3>
                    <p>As the App processes your location, we are committed to protecting your privacy in compliance with the General Data Protection Regulation (GDPR).</p>

                    <p><strong>1. Data Controller & Data Protection Officer (DPO)</strong><br>
                    The legal responsibility for data processing lies jointly with the Universidade de Aveiro and ITAv.<br>
                    <strong>Data Controller:</strong> Universidade de Aveiro / ITAv Campus Universitário de Santiago, 3810 - 193 Aveiro – Portugal<br>
                    <strong>Contact Email:</strong> it@lx.it.pt<br>
                    <strong>Data Protection Officer (DPO):</strong> For any doubts regarding data protection, you can contact the DPO via email at filipeviseu@ua.pt</p>

                    <p><strong>2. What Data We Collect</strong><br>
                    To provide the Service, we collect:</p>
                    <ul>
                        <li><strong>Telemetry and Location Data:</strong> Your vehicle's precise GPS coordinates, heading, and speed.</li>
                        <li><strong>Device/System Data:</strong> Device identifiers necessary for Android Automotive OS to communicate with Eclipse Hono and the Mosquitto message broker.</li>
                        <li><strong>Usage Data:</strong> Interactions with the App (e.g., routing choices, acknowledged alerts).</li>
                    </ul>

                    <p><strong>3. Data Sovereignty & Self-Hosted Infrastructure</strong><br>
                    Your privacy is protected by structural design. The core backend services utilized by this App (Eclipse Ditto, Eclipse Hono, Mosquitto Brokers) are entirely self-hosted within the ITAv infrastructure. Your telemetry and digital twin data <u>does not leave the ITAv network</u> and is not shared with external cloud providers (e.g., AWS, Google Cloud) for processing.</p>

                    <p><strong>4. Legal Basis and Purpose for Processing</strong><br>
                    We process your data based on your Explicit Consent (Article 6(1)(a) GDPR). The data is used strictly for:</p>
                    <ul>
                        <li>Creating a digital twin in Eclipse Ditto to provide you with real-time hazard, speeding, and overtaking alerts.</li>
                        <li>Calculating routing and navigation.</li>
                        <li>Academic research to identify traffic patterns and improve event-detection algorithms.</li>
                    </ul>

                    <p><strong>5. Data Anonymization and Retention</strong><br>
                    We do not sell your data or share it with commercial third parties.</p>
                    <ul>
                        <li><strong>Active Use:</strong> Your precise location is processed in real-time to provide the service.</li>
                        <li><strong>Storage and Anonymization:</strong> Historical telemetry data used for academic research is stripped of direct identifiers (pseudonymized) when stored.</li>
                        <li><strong>End of Project:</strong> Upon the official conclusion of this academic project (estimated July 2026), or if the Service is discontinued, all personal and location data linking back to you or your specific vehicle will be permanently deleted. Any remaining data kept for academic publication will be strictly and irreversibly aggregated so that it cannot be linked back to any individual.</li>
                    </ul>

                    <p><strong>6. Your User Rights</strong><br>
                    Under the GDPR, you have the right to:</p>
                    <ul>
                        <li><strong>Access:</strong> Request a copy of the personal data we hold about you.</li>
                        <li><strong>Rectification:</strong> Request correction of inaccurate data.</li>
                        <li><strong>Erasure ("Right to be Forgotten"):</strong> Request the deletion of your data. You can delete your account/data via the App settings at any time.</li>
                        <li><strong>Withdraw Consent:</strong> You may withdraw your consent for location tracking at any time by disabling location permissions in your vehicle's Android Automotive OS settings, though this will render the App non-functional.</li>
                        <li><strong>Lodge a Complaint:</strong> If you believe your rights are being violated, you have the right to file a complaint with the Portuguese Data Protection Authority (Comissão Nacional de Proteção de Dados - CNPD).</li>
                    </ul>

                    <p><strong>7. Contact Us</strong><br>
                    For any questions regarding these Terms, your privacy, or to exercise your GDPR rights, please contact the development team at: pei.automotiveapp.ua@gmail.com</p>

                    <p><strong>8. Changes to this Policy</strong><br>
                    If we make material changes to this policy, we will notify you via an in-app alert before the changes take effect.</p>
                </div>

                <button type="button" onclick="toggleModal()" class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!}" style="width: 100%; padding: 12px; font-weight: bold;">CLOSE</button>
            </div>
        </div>

        <script>
            // Toggle the Modal window
            function toggleModal() {
                const modal = document.getElementById('termsModal');
                modal.style.display = (modal.style.display === 'none' || modal.style.display === '') ? 'block' : 'none';
            }

            document.addEventListener("DOMContentLoaded", function() {
                const checkbox = document.getElementById('termsAccepted');
                const btn = document.getElementById('kc-register-button');
                
                // Toggle Button logic
                checkbox.addEventListener('change', function() {
                    btn.disabled = !this.checked;
                    btn.style.opacity = this.checked ? "1" : "0.5";
                    btn.style.cursor = this.checked ? "pointer" : "not-allowed";
                });
            });
        </script>
    </#if>
</@layout.registrationLayout>