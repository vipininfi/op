(function () {
    // Inject Tailwind (if not present)
    const tailwind = document.createElement("link");
    tailwind.href = "https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css";
    tailwind.rel = "stylesheet";
    document.head.appendChild(tailwind);
  
    // Custom style
    const style = document.createElement("style");
    style.innerHTML = `
      #chat-box{background:#57626a;}
      #chat-toggle-btn{background:#57626a;}
    `;
    document.head.appendChild(style);
  
    // Chat widget HTML
    const html = `
      <div id="chat-toggle-btn"
        class="fixed bottom-4 right-4 bg-gray-700 text-white rounded-full w-12 h-12 flex items-center justify-center shadow-md cursor-pointer hover:bg-gray-600 transition-colors duration-200"
        onclick="window.PksBot.toggleChat()">
        💬
      </div>
      <div id="chat-box" style="height: 50vh;"
        class="fixed bottom-20 right-4 w-80 h-[50vh] bg-white border border-gray-300 rounded-lg shadow-md transform transition-all duration-300 scale-0 opacity-0 flex flex-col z-50 pointer-events-none">
        <div class="flex items-center justify-between px-3 py-2 bg-gray-700 text-white rounded-t-lg">
          <h3 class="text-xs font-semibold">PksBot</h3>
          <button onclick="window.PksBot.toggleChat()" class="text-white hover:text-gray-300">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div id="chat-messages" class="flex-1 overflow-y-auto p-2 space-y-1 bg-gray-50 text-xs"></div>
        <div class="p-2 border-t bg-white">
          <div class="flex space-x-1">
            <textarea id="chat-input"
              class="flex-1 border border-gray-300 rounded p-1 text-xs focus:outline-none focus:ring-1 focus:ring-gray-500 resize-none"
              rows="2" placeholder="Ask something..."></textarea>
            <button id="sendButton"
              class="bg-gray-700 text-white p-1 rounded hover:bg-gray-600 transition-colors duration-200">
              <svg class="w-10 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    `;
  
    // Inject widget
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    document.body.appendChild(wrapper);
  
    // Bot logic
    const PksBot = {
      toggleChat() {
        const chatBox = document.getElementById("chat-box");
        chatBox.classList.toggle("scale-0");
        chatBox.classList.toggle("opacity-0");
        chatBox.classList.toggle("pointer-events-none");
        chatBox.classList.toggle("scale-100");
        chatBox.classList.toggle("opacity-100");
      },
  
      appendMessage(text, sender = "bot", isLoading = false, isHTML = false) {
        const chat = document.getElementById("chat-messages");
        const msg = document.createElement("div");
        msg.className = `p-1 rounded ${sender === "user" ? "bg-gray-700 text-white ml-auto" : "bg-white text-gray-800"}`;
  
        if (isLoading) {
          msg.className = "bg-white p-1 rounded flex space-x-1";
          msg.innerHTML = `
            <div class="w-1.5 h-1.5 rounded-full bg-gray-700 animate-pulse"></div>
            <div class="w-1.5 h-1.5 rounded-full bg-gray-700 animate-pulse delay-200"></div>
            <div class="w-1.5 h-1.5 rounded-full bg-gray-700 animate-pulse delay-400"></div>
          `;
          msg.id = "loading-message";
        } else {
          msg.innerHTML = isHTML ? text : `<div class="whitespace-pre-wrap">${text}</div>`;
        }
  
        chat.appendChild(msg);
        chat.scrollTop = chat.scrollHeight;
      },
  
      sendMessage() {
        const input = document.getElementById("chat-input");
        const message = input.value.trim();
        const sendButton = document.getElementById("sendButton");
  
        if (!message) return;
  
        input.disabled = true;
        sendButton.disabled = true;
  
        this.appendMessage(message, "user");
        input.value = "";
        this.appendMessage("", "bot", true);
  
        fetch("https://yourdomain.com/api/chatbot/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ message })
        })
          .then(res => res.json())
          .then(data => {
            document.getElementById("loading-message")?.remove();
            const response = data.response || "No response";
            if (response.includes("<table>")) {
              this.appendMessage(response, "bot", false, true);
            } else {
              this.appendMessage(response, "bot");
            }
          })
          .catch(() => {
            document.getElementById("loading-message")?.remove();
            this.appendMessage("⚠️ Error", "bot");
          })
          .finally(() => {
            input.disabled = false;
            sendButton.disabled = false;
            input.focus();
          });
      }
    };
  
    // Global binding
    window.PksBot = PksBot;
  
    // Input shortcut
    setTimeout(() => {
      document.getElementById("chat-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          window.PksBot.sendMessage();
        }
      });
  
      document.getElementById("sendButton").addEventListener("click", () => {
        window.PksBot.sendMessage();
      });
    }, 500); // Wait till DOM is injected
  
  })();
  