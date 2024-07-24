# Hypha RPC

## Usage

### Connect to Hypha

```javascript
import { hyphaWebsocketClient } from "hypha-rpc";

hyphaWebsocketClient.connectToServer({
  server_url: 'https://ai.imjoy.io',
}).then(async (api)=>{
  await api.register_service(
      {
          "id": "echo-service",
          "config":{
              "visibility": "public"
          },
          "type": "echo",
          echo( data ){
              console.log("Echo: ", data)
              return data
          }
      }
  )
})
```